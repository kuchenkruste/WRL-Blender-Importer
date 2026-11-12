# ==========================================================================
# LEGO Racers 2 World Importer for Blender
#
# Copyright (C) 2026  Kuchenkruste
# Copyright (C) 2026  Fluffy Cupcake (modifications, see "Modification notices" below)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# --------------------------------------------------------------------------
# Attribution
#
# The WRL entity/property tables (WRL_FORMATS), the WRL container parser
# (parse_wrl), the MDL2 model parser (parse_mdl2, _load_mdl2_vertices) and
# the TDF terrain parser including its multi-texture blend math
# (parse_tdf, build_terrain_material) are Python reimplementations based on
# the C++ source of the GiantBlargg/Whirled project
# (https://github.com/GiantBlargg/Whirled), used with the explicit
# permission of its author, GiantBlargg.
#
# The MIP/TGA texture decoder (decode_mip_image) was independently derived
# by observing the behaviour of dead_name's LR2ModelViewer/LibLR2.dll tool
# via clean-room .NET IL disassembly (a custom ECMA-335 metadata reader and
# IL disassembler written from scratch for this purpose) - no code from
# that tool was copied.
#
# The Rock Raiders United community thread "All About WRLs" (contributors
# including dead_name and Fluffy Cupcake) provided early conceptual
# groundwork on the WRL format that helped guide this independent
# implementation.
#
# All Blender-side integration code (bmesh/material/image construction,
# the import operator, layer/zone collection organisation, binding-based
# parenting, etc.) is original work for this addon.
#
# Parts of this addon's source code were developed with the assistance of
# an AI coding tool, used under the author's direction and subject to the
# author's own design decisions, testing, and verification.
#
# This project has no affiliation with the LEGO Group and is not endorsed
# by it. LEGO Racers 2 game assets are copyrighted by their original
# rights holders and are not included with, nor required to be
# distributed alongside, this addon's source code.
#
# --------------------------------------------------------------------------
# Modification notices (GNU GPL v3, Section 5(a))
#
# This file is a modified version of "lr2_import_addon.py" (version 1.0.0,
# author "Kuchenkruste"). It was modified by "Fluffy Cupcake" on 18.09.2026
# (18 September 2026) and carries version number 1.3.0 as a result.
# Every modified or added passage is marked in the source with a comment
# containing "[Fluffy Cupcake, 18.09.2026]" (CHANGED / BEGIN ... END).
# Further modifications by Kuchenkruste of 11.11.2026 and 12.11.2026 are marked
# "[Kuchenkruste, 11.11.2026]" / "[Kuchenkruste, 12.11.2026]".
#
# Changes by Fluffy Cupcake:
#   * Supports land patches (cPatch)
#   * Supports Sea (cSea)
#   * Supports WaterCourse (cWaterCourse)
#   * Supports Water Planes (cWaterSheet)
#   * Has the correct X axis (AXIS_MATRIX)
#   * Game Data path is auto-detected based on the selected WRL
#   * Replaced the "skip plasball" toggle with "skip races" (works via
#     layer detection)
#   * MDL0 patch-model parser (parse_mdl0) and MDL0 dispatch in parse_mdl2
#   * IFL image-sequence resolution for Sea textures
#   * Import log file (<world>_lr2_import.log) and extended diagnostics
#   * New import options (Sea Texture Tiles, WaterCourse UV factors)
#   * Triangle winding order changed in build_object
#   * Model parse/"not found" messages are now only logged for cPatch models
#
#   NOTE: According to the author, only tested with Blender 4.5.14 LTS
#
# Changes by Kuchenkruste (11.11.2026 / 12.11.2026):
#   * Unified terrain material (single material for the whole terrain) and
#     optional terrain collision copy
#   * Objects are sorted into the game's layer sets (Static, Track N, Bonus,
#     Battle, Foyer, Explore) read from the WRL's layer manager, with the
#     object class as second level; "Skip Layer Sets" options
#   * cCheckPoint support, optional special markers (start positions etc.)
#   * Import panel reorganised into sections; "Group Paired Layers into
#     Zones" and the layer-detection based "Skip Races" were replaced
#
#   NOTE: Tested with Blender 5.2
#   
# ==========================================================================

bl_info = {
    "name": "LEGO Racers 2 World Importer",
    "author": "Kuchenkruste",
    "version": (1, 4, 0),
    "blender": (3, 2, 0),
    "location": "File > Import > LEGO Racers 2 World (.wrl)",
    "description": "Imports LR2 .wrl worlds (models + terrain + textures) directly, "
                   "including procedural cWaterCourse geometry using recovered LR2 Bezier/grid construction.",
    "category": "Import-Export",
}

import bpy
import bmesh
import math
import os
import struct
# [Fluffy Cupcake, 18.09.2026] CHANGED: new import (used by the added error logging).
import traceback
from pathlib import Path

# [Fluffy Cupcake, 18.09.2026] CHANGED: FloatProperty added (new Sea/WaterCourse import options).
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ImportHelper


# >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: import log file helpers (set_import_log, log).
# ==========================================================================
# Import diagnostics
# ==========================================================================

_LOG_FILE = None
_LOG_LINES = []


def set_import_log(path):
    global _LOG_FILE, _LOG_LINES
    _LOG_FILE = Path(path) if path else None
    _LOG_LINES = []
    if _LOG_FILE:
        try:
            _LOG_FILE.write_text("", encoding="utf-8")
        except OSError:
            pass


def log(msg):
    text = str(msg)
    _LOG_LINES.append(text)
    print(text)
    if _LOG_FILE:
        try:
            with _LOG_FILE.open("a", encoding="utf-8") as fp:
                fp.write(text + "\n")
        except OSError:
            pass


# <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: import log file helpers.
# ==========================================================================
# Binary read helpers
# ==========================================================================


def read_u32(f):
    return struct.unpack("<I", f.read(4))[0]


def read_i32(f):
    return struct.unpack("<i", f.read(4))[0]


def read_u16(f):
    return struct.unpack("<H", f.read(2))[0]


def read_u8(f):
    return struct.unpack("<B", f.read(1))[0]


def read_i8(f):
    return struct.unpack("<b", f.read(1))[0]


def read_f32(f):
    return struct.unpack("<f", f.read(4))[0]


def read_string(f, length):
    data = f.read(length)
    return data.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def read_vec2(f):
    return (read_f32(f), read_f32(f))


def read_vec3(f):
    return (read_f32(f), read_f32(f), read_f32(f))


def read_quat(f):
    return (read_f32(f), read_f32(f), read_f32(f), read_f32(f))


def read_colour(f):
    return (read_f32(f), read_f32(f), read_f32(f), read_f32(f))


# ==========================================================================
# Coordinate conversion (LR2 Y-up -> Blender Z-up), pure math, no bpy needed
# ==========================================================================

# [Fluffy Cupcake, 18.09.2026] CHANGED: X axis corrected (previously ((-1, 0, 0), (0, 0, 1), (0, 1, 0))).
AXIS_MATRIX = ((1, 0, 0), (0, 0, 1), (0, 1, 0))


def mat3_transpose(m):
    return tuple(tuple(m[j][i] for j in range(3)) for i in range(3))


def mat3_mul(a, b):
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )


def mat3_mul_vec(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def mat3_from_quat(q):
    x, y, z, w = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (
        (1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)),
        (2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)),
        (2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)),
    )


def quat_from_mat3(m):
    (m00, m01, m02), (m10, m11, m12), (m20, m21, m22) = m
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    return (x, y, z, w)


def convert_position(pos):
    return mat3_mul_vec(AXIS_MATRIX, pos)


def convert_direction(v):
    return mat3_mul_vec(AXIS_MATRIX, v)


def convert_rotation(quat):
    r = mat3_from_quat(quat)
    m = AXIS_MATRIX
    mt = mat3_transpose(m)
    r2 = mat3_mul(mat3_mul(m, r), mt)
    return quat_from_mat3(r2)


# ==========================================================================
# WRL format definitions + parser
# ==========================================================================

COMMON_PROPS = [("layer", "int", None), ("name", "string", 24)]
WRL_FORMATS = {}


def _reg(type_id, u, props, model_prop=None):
    WRL_FORMATS[(type_id, u)] = {"props": COMMON_PROPS + props, "model": model_prop}


_STATIC_PROPS = [
    ("binding", "string", 24), ("position", "vec3", None), ("rotation", "quat", None),
    ("_1", "float", None), ("_2", "float", None), ("collision_sound", "int", None),
    ("model", "string", 0x80),
]
_reg("cGeneralStatic", 0, _STATIC_PROPS, "model")
_reg("cGoldenBrick", 1, _STATIC_PROPS, "model")
_reg("cBonusVortex", 1, _STATIC_PROPS + [("difficulty", "int", None)], "model")
_reg("cThePits", 1, _STATIC_PROPS + [("_3", "float", None)], "model")
_reg("cWeaponPickup", 1, _STATIC_PROPS + [("_3", "float", None)], "model")
# >>> [Kuchenkruste, 11.11.2026] BEGIN of added/changed code: cCheckPoint (derived from cGeneralStatic; verified against SANDY.WRL).
# The "binding" slot holds the owning "CP Supervisor Track N"; body+0xDC is the checkpoint order index.
_reg("cCheckPoint", 0, _STATIC_PROPS + [("checkpoint_index", "int", None)], "model")

# Entities without a model that share the layout [layer][name][binding][position][quaternion]
# (checked in SANDY.WRL). They are imported as Empties ("markers") so they appear in the layer hierarchy.
_MARKER_PROPS = [("binding", "string", 24), ("position", "vec3", None), ("rotation", "quat", None)]
MARKER_TYPES = ("cRaceStartPos", "cFoyerStartPos", "cCinematicCamera", "cReverbGateway")
for _t in MARKER_TYPES:
    for _u in range(0, 6):
        WRL_FORMATS[(_t, _u)] = {"props": COMMON_PROPS + _MARKER_PROPS, "model": None, "marker": True}
# <<< [Kuchenkruste, 11.11.2026] END of added/changed code.

_MOBILE_PROPS = [
    ("binding", "string", 24), ("position", "vec3", None), ("rotation", "quat", None),
    ("_1", "float", None), ("_2", "float", None), ("collision_sound", "int", None),
    ("weight", "float", None), ("_3", "vec3", None), ("model", "string", 0x80), ("_4", "int", None),
]
_reg("cGeneralMobile", 0, _MOBILE_PROPS, "model")
_reg("cBonusPickup", 1, _MOBILE_PROPS, "model")

_TERRAIN_PROPS = (
    [
        ("binding", "string", 24), ("position", "vec3", None), ("rotation", "quat", None),
        ("_1", "float", None), ("_2", "float", None), ("_3", "int", None),
        ("model", "string", 0x80), ("_4", "int", None), ("scale", "vec3", None),
        ("_5", "int", None), ("_6", "int", None), ("_7", "int", None), ("_8", "int", None),
        ("texture_scale", "vec2", None),
    ]
    + [(f"_{i}", "int", None) for i in range(9, 33)]
)
_reg("cLegoTerrain", 3, _TERRAIN_PROPS, "model")
_reg("cSkyBox", 0, [("binding", "string", 24), ("model", "string", 0x80)], "model")

# >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: cPatch, cWaterCourse, cSea and cWaterSheet entity registrations.
# cPatch / OBMGcPatch (land patches, e.g. Arctic ice sheets).
# The transform is the standard position/rotation/scale sequence in the
# payload. A 32-bit value follows the scale at payload 0x58; the model path
# is then stored as a fixed 0x80-byte string beginning at payload 0x5C.

_reg("cPatch", 4, [], None)

# cWaterCourse / OBMGcWaterCourse (procedural flat water ribbons).
# The supplied DINO WRLs use u=0. The payload has two 0x80-byte texture
# fields, followed by a course-level vec3 and several material/runtime fields.
# The experimentally confirmed geometry section is:
#   payload + 0x1B4 : u32 point count
#   payload + 0x1B8 : point[0]
#   point stride     : 20 bytes
#   point            : float x, y, z, float unknown(usually 1.0), float width
_reg("cWaterCourse", 0, [], None)

# cSea documented layout (offsets include the entity header):
# Binding 40-57, material 70-?, Height 170-173, Rise/Lower 174-177,
# Fade Out 178-17B, RGBS Tint 17C-17F.
_SEA_PROPS = [
    ("binding", "string", 24),       # 40-57
    ("_sea_ref", "string", 24),      # 58-6F
    ("sea_texture", "string", 0x80), # 70-EF
    ("_sea_unknown", "bytes", 0x80),
    ("sea_height", "float", None),
    ("sea_rise_lower", "float", None),
    ("sea_fade_distance", "float", None),
    ("sea_rgb_s", "bytes", 4),
    ("_sea_tail", "bytes", 4),
]
_reg("cSea", 2, _SEA_PROPS, None)

# Water sheets are procedural planes stored directly in the WRL rather than MD2 models.
# The 24-byte field between the name and texture paths is currently undocumented.
_WATER_SHEET_PROPS = [
    ("_water_unknown", "bytes", 24),
    ("water_texture", "string", 0x80),
    ("reflection_texture", "string", 0x80),
    ("position", "vec3", None),
    ("rotation", "quat", None),
    ("_water_unknown2", "bytes", 8),
    ("width", "float", None),
    ("height", "float", None),
    ("_water_unknown3", "bytes", 116),
]
_reg("cWaterSheet", 2, _WATER_SHEET_PROPS, None)

# <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: entity registrations.
WRL_MAGIC = 0x57324352
WRL_VERSION = 0xB
OBMG_MAGIC = 0x474D424F


class Entity:
    __slots__ = ("type_id", "u", "layer", "name", "props")

    def __init__(self, type_id, u, props):
        self.type_id = type_id
        self.u = u
        self.props = props
        self.layer = props.get("layer", 0)
        self.name = props.get("name", "")


def _read_prop(f, kind, length):
    if kind == "int":
        return read_i32(f)
    if kind == "float":
        return read_f32(f)
    if kind == "string":
        return read_string(f, length)
    if kind == "vec2":
        return read_vec2(f)
    if kind == "vec3":
        return read_vec3(f)
    if kind == "quat":
        return read_quat(f)
    # [Fluffy Cupcake, 18.09.2026] CHANGED: new property kind "bytes" (raw byte fields).
    if kind == "bytes":
        return f.read(length)
    raise ValueError(f"Unknown property kind: {kind}")


# >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: parsers for cPatch (land patch) and cWaterCourse.
def _parse_cpatch(f, next_chunk):
    """Parse OBMGcPatch / cPatch.

    Verified payload layout in the supplied Arctic.wrl cPatch records:
      0x00  layer (i32)
      0x04  name[24]
      0x1C  binding[24]
      0x34  position[3]
      0x40  rotation quaternion[4]
      0x50  scale[2] (X/Y only)
      0x58  unknown u32 (currently 13 in the Arctic records)
      0x5C  model path[0x80]

    The remaining bytes are editor/material metadata and are retained as
    raw data rather than guessed at.
    """
    props = {}
    props["layer"] = read_i32(f)
    props["name"] = read_string(f, 24)
    props["binding"] = read_string(f, 24)
    props["position"] = read_vec3(f)
    props["rotation"] = read_quat(f)

    # cPatch stores only planar X/Y scale here. The next DWORD at 0x58 is
    # separate metadata, followed immediately by the fixed 0x80-byte path.
    # Reading a third scale float consumes those metadata bytes and shifts
    # the path, which was the source of the truncated "itor gen\patches..."
    # reference and the absurd scale values seen in the previous log.
    sx = read_f32(f)
    sy = read_f32(f)
    props["scale"] = (sx, sy, 1.0)
    props["_patch_model_meta"] = read_u32(f)
    props["model"] = read_string(f, 0x80).strip()
    log(f"[LR2 Parse] cPatch name={props['name']!r} layer={props['layer']} "
        f"pos={props['position']} scale={props['scale']} "
        f"meta={props['_patch_model_meta']} model={props['model']!r}")

    # Preserve the remaining editor/material data for diagnostics without
    # interpreting undocumented fields.
    props["_patch_raw"] = f.read(max(0, next_chunk - f.tell()))
    return props



def _parse_cwatercourse(f, next_chunk):
    """Parse cWaterCourse using the recovered LibLR2 field order.

    Payload layout relative to the start of the entity data:
      0x00  layer
      0x04  name[24]
      0x1C  binding[24]
      0x34  BottomTexturePath[0x80]
      0xB4  TopTexturePath[0x80]
      0x134 Position[3 floats]
      0x140 MatProps[0x58 bytes]
      0x198 BottomLayerSpeed
      0x19C TopLayerSpeed
      0x1A0 Wavyness
      0x1A4 TextureDisturbance
      0x1A8 TransparentEdges
      0x1AC GridResolution
      0x1B0 FlowDirection
      0x1B4 ControlPoint count
      0x1B8 ControlPoints, 20 bytes each:
            Vec3 Position, float VOffset, float Width

    The 0x58-byte MatProps block is intentionally left as raw bytes here;
    its structure is defined by LibLR2 separately from WaterCourse itself.
    """
    start = f.tell()

    def at(offset, reader):
        here = f.tell()
        f.seek(start + offset)
        value = reader()
        f.seek(here)
        return value

    props = {}
    props["layer"] = at(0x00, lambda: read_i32(f))
    props["name"] = at(0x04, lambda: read_string(f, 24))
    props["binding"] = at(0x1C, lambda: read_string(f, 24))
    props["bed_texture"] = at(0x34, lambda: read_string(f, 0x80)).strip()
    props["water_texture"] = at(0xB4, lambda: read_string(f, 0x80)).strip()
    props["course_ref"] = at(0x134, lambda: read_vec3(f))
    props["matprops_raw"] = at(0x140, lambda: f.read(0x58))
    # MatProps +0x40 (course payload 0x180) is the empirically verified
    # water-course texture tiling field (100.0 in both DINO courses).
    props["texture_tiling"] = at(0x180, lambda: read_f32(f))
    props["texture_tiling_aux"] = at(0x184, lambda: read_f32(f))
    props["bottom_layer_speed"] = at(0x198, lambda: read_f32(f))
    props["top_layer_speed"] = at(0x19C, lambda: read_f32(f))
    props["wavyness"] = at(0x1A0, lambda: read_f32(f))
    props["texture_disturbance"] = at(0x1A4, lambda: read_f32(f))
    props["transparent_edges"] = at(0x1A8, lambda: read_u32(f))
    props["grid_resolution"] = at(0x1AC, lambda: read_i32(f))
    props["flow_direction"] = at(0x1B0, lambda: read_u32(f))
    point_count = at(0x1B4, lambda: read_i32(f))

    point_start = start + 0x1B8
    max_points = max(0, (next_chunk - point_start) // 20)
    if point_count < 0:
        point_count = 0
    if point_count > max_points:
        log(f"[LR2 Parse] cWaterCourse {props['name']!r}: point count {point_count} exceeds "
            f"chunk capacity {max_points}; clamping")
        point_count = max_points

    points = []
    f.seek(point_start)
    for _ in range(point_count):
        pos = read_vec3(f)
        v_offset = read_f32(f)
        width = read_f32(f)
        points.append({
            "position": pos,
            "v_offset": v_offset,
            "width": width,
        })

    props["points"] = points
    props["point_count"] = len(points)

    log(f"[LR2 Parse] cWaterCourse name={props['name']!r} layer={props['layer']} "
        f"points={len(points)} grid_res={props['grid_resolution']} flow_dir={props['flow_direction']} "
        f"bottom_speed={props['bottom_layer_speed']:.6g} top_speed={props['top_layer_speed']:.6g} "
        f"wave={props['wavyness']:.6g} disturbance={props['texture_disturbance']:.6g} "
        f"texture_tiling={props['texture_tiling']:.6g}")

    f.seek(start)
    props["_course_raw"] = f.read(max(0, next_chunk - start))
    f.seek(next_chunk)
    return props

# <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: cPatch / cWaterCourse parsers.
# >>> [Kuchenkruste, 11.11.2026] BEGIN of added/changed code: cLayerManager ("ceLayerManager") layer sets.
# Verified against SANDY.WRL / ARCTIC.WRL: 10 named sets; names at body+0x438 (10 x 24 bytes),
# 256-bit masks at body+0x2F8 (10 x 32 bytes). The engine instantiates an entity of a set when bit
# ((layer >> 5) & 0xFF) of the set's mask is set (World_InstantiateLayerSet in LEGO Racers 2.exe).
LM_MASKS_OFFSET = 0x2F8
LM_NAMES_OFFSET = 0x438
LM_SET_COUNT = 10


def layer_index(layer_value):
    return (int(layer_value) >> 5) & 0xFF


def _parse_clayermanager(f, next_chunk):
    start = f.tell()
    body = f.read(next_chunk - start)
    props = {"layer": struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else 0,
             "name": body[4:28].split(b"\x00", 1)[0].decode("utf-8", errors="replace") if len(body) >= 28 else ""}
    sets = []
    if len(body) >= LM_NAMES_OFFSET + LM_SET_COUNT * 24:
        for i in range(LM_SET_COUNT):
            raw = body[LM_NAMES_OFFSET + i * 24: LM_NAMES_OFFSET + (i + 1) * 24]
            name = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
            mask = body[LM_MASKS_OFFSET + i * 32: LM_MASKS_OFFSET + (i + 1) * 32]
            bits = {k for k in range(256) if (mask[k // 8] >> (k % 8)) & 1}
            if name:
                sets.append((name, bits))
    props["layer_sets"] = sets
    return props
# <<< [Kuchenkruste, 11.11.2026] END of added/changed code.


def parse_wrl(path):
    entities = []
    with open(path, "rb") as f:
        magic = read_u32(f)
        if magic != WRL_MAGIC:
            raise ValueError(f"Not a WRL file (magic={magic:#010x})")
        version = read_u32(f)  # noqa: F841 - not currently validated strictly
        f.seek(0, os.SEEK_END)
        file_len = f.tell()
        f.seek(8)
        while f.tell() < file_len:
            chunk_start = f.tell()
            chunk_magic = read_u32(f)
            if chunk_magic != OBMG_MAGIC:
                raise ValueError(f"Corrupt WRL: missing OBMG header at offset {chunk_start:#x}")
            type_id = read_string(f, 24)
            u = read_u32(f)
            length = read_u32(f)
            data_start = f.tell()
            next_chunk = data_start + length

            spec = WRL_FORMATS.get((type_id, u))
            props = {}
            # [Fluffy Cupcake, 18.09.2026] CHANGED: cPatch and cWaterCourse are routed to their own parsers (next 4 lines); the former 'if spec is not None' became 'elif'.
            if type_id == "cPatch":
                props = _parse_cpatch(f, next_chunk)
            elif type_id == "cWaterCourse":
                props = _parse_cwatercourse(f, next_chunk)
            elif type_id == "cLayerManager":
                props = _parse_clayermanager(f, next_chunk)
            elif spec is not None:
                for name, kind, length_arg in spec["props"]:
                    props[name] = _read_prop(f, kind, length_arg)
            else:
                for name, kind, length_arg in COMMON_PROPS:
                    props[name] = _read_prop(f, kind, length_arg)
                remaining = next_chunk - f.tell()
                if remaining > 0:
                    f.read(remaining)

            entities.append(Entity(type_id, u, props))
            if f.tell() != next_chunk:
                f.seek(next_chunk)
    return entities


# ==========================================================================
# [Fluffy Cupcake, 18.09.2026] CHANGED: section title extended to MDL0 (was 'MDL2 (.md2) model parsing').
# MDL2 / MDL0 (.md2) model parsing
# ==========================================================================

MDL2_END = 0
MDL2_MDL1 = 0x314C444D
MDL2_MDL2 = 0x324C444D
MDL2_GEO1 = 0x314F4547

VF_VECTOR = 1 << 0
VF_NORMAL = 1 << 1
VF_UV = 1 << 3


def _load_mdl2_vertices(f):
    vertex_vector_offset = read_u32(f)
    vertex_normal_offset = read_u32(f)
    read_u32(f)  # vertex_colour_offset (unused - no VF_COLOUR support)
    vertex_texcoord_offset = read_u32(f)
    vertex_size = read_u32(f)
    texcoord_count = read_u32(f)
    flags = read_u16(f)
    vertices_count = read_u16(f)

    start_position = f.tell() + 12
    vectors = [(0.0, 0.0, 0.0)] * vertices_count
    normals = [None] * vertices_count
    uvs = [None] * vertices_count

    for v in range(vertices_count):
        if flags & VF_VECTOR:
            f.seek(start_position + v * vertex_size + vertex_vector_offset)
            vectors[v] = read_vec3(f)
        if flags & VF_NORMAL:
            f.seek(start_position + v * vertex_size + vertex_normal_offset)
            normals[v] = read_vec3(f)
        if flags & VF_UV:
            f.seek(start_position + v * vertex_size + vertex_texcoord_offset)
            uvs[v] = read_vec2(f)
            if texcoord_count > 1:
                read_vec2(f)

    f.seek(start_position + vertices_count * vertex_size)
    return vectors, normals, uvs



# >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: new MDL0 patch-model parser (parse_mdl0).
def parse_mdl0(path):
    """Parse the LR2/ATD MDL0 patch model format.

    MDL0 is a simpler, sequential model format than MDL2.  LR2 uses it for
    editor-generated patch meshes.  The important wrinkle for Arctic patches
    is fill_type == 1: the polygon stream is a triangle strip, so it contains
    (polygon_count + 2) vertex records rather than polygon_count * 3 records.
    """
    surfaces = []
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"MDL0":
            raise ValueError(f"Not an MDL0 file: {magic!r}")

        # Header used by the LR2 MDL0 files.  These are little-endian.
        _zero0, _zero1, _zero2, _float0, bitmap_count = struct.unpack(
            "<3IfI", f.read(20)
        )

        textures = []
        for _ in range(bitmap_count):
            textures.append(read_string(f, 256))

        # Render-group/model metadata.  The two H values are polygon and
        # vertex counts respectively in the supplied LR2 MDL0 files.
        meta = struct.unpack("<8If3I2H4I", f.read(68))
        polygons = meta[12]
        vertices_count = meta[13]

        # Texture-blend metadata (one header + four blend records).
        f.read(8)
        f.read(32)

        # Vertex buffer descriptor.
        (
            vertex_offset_vector,
            vertex_offset_normal,
            vertex_offset_colour,
            vertex_offset_texcoord,
            vertex_size,
            texcoord_count,
            vertex_flags,
            reported_vertices,
            _managed_buffer,
            _current_vertex,
        ) = struct.unpack("<4I2I4H", f.read(32))
        f.read(8)  # reserved

        # In LR2 these patch models report the actual vertex count correctly;
        # keep the header's polygon/vertex values as the authoritative counts.
        vertices_count = int(vertices_count)
        vertex_size = int(vertex_size)
        if vertices_count <= 0 or vertex_size <= 0:
            raise ValueError(f"Invalid MDL0 vertex descriptor: {vertices_count=} {vertex_size=}")

        verts = []
        norms = []
        uvs = []
        has_normals = bool(vertex_flags & 0b0010)
        has_uv = bool(vertex_flags & 0b1000)
        has_color = bool(vertex_flags & 0b0100)

        vertex_data_start = f.tell()
        for i in range(vertices_count):
            raw = f.read(vertex_size)
            if len(raw) != vertex_size:
                raise ValueError(f"Truncated MDL0 vertex buffer at vertex {i}")

            # Offsets in the descriptor are byte offsets within each vertex.
            if vertex_offset_vector + 12 > vertex_size:
                raise ValueError("MDL0 vector offset is outside vertex structure")
            v = struct.unpack_from("<3f", raw, vertex_offset_vector)
            verts.append(v)

            if has_normals and vertex_offset_normal + 12 <= vertex_size:
                norms.append(struct.unpack_from("<3f", raw, vertex_offset_normal))
            else:
                norms.append(None)

            if has_uv and vertex_offset_texcoord + 8 <= vertex_size:
                u = struct.unpack_from("<2f", raw, vertex_offset_texcoord)
                uvs.append(u)
            else:
                uvs.append(None)

        fill_type_raw = f.read(4)
        if len(fill_type_raw) != 4:
            raise ValueError("Missing MDL0 fill type")
        fill_type = struct.unpack("<I", fill_type_raw)[0]

        # The Arctic models are the documented LR2 exception: fill_type 1 is
        # a triangle strip.  For N triangles, N+2 vertex references follow.
        if fill_type == 0:
            index_count = polygons * 3
            indices = list(struct.unpack("<%dH" % index_count, f.read(index_count * 2))) if index_count else []
        elif fill_type == 1:
            index_count = polygons + 2
            indices = list(struct.unpack("<%dH" % index_count, f.read(index_count * 2))) if index_count else []
        else:
            raise ValueError(f"Unsupported LR2 MDL0 fill type {fill_type}")

        if any(i >= len(verts) for i in indices):
            bad = max(i for i in indices if i >= len(verts))
            raise ValueError(
                f"MDL0 index {bad} exceeds vertex count {len(verts)}"
            )

        texture = textures[0] if textures else None
        surfaces.append({
            "vertices": verts,
            "normals": norms,
            "uvs": uvs,
            "indices": indices,
            "fill_type": fill_type,
            "texture": texture,
            "material": None,
        })

        log(
            f"[LR2 Import] MDL0: {Path(path).name}: "
            f"vertices={len(verts)}, polygons={polygons}, fill_type={fill_type}, "
            f"indices={len(indices)}"
        )

    return surfaces

# <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: parse_mdl0.
def parse_mdl2(path):
    # [Fluffy Cupcake, 18.09.2026] CHANGED: parse_mdl2 now detects the MDL0 magic and dispatches to parse_mdl0 (next 4 lines).
    with open(path, "rb") as _mf:
        magic = _mf.read(4)
    if magic == b"MDL0":
        return parse_mdl0(path)

    surfaces = []
    textures = []
    materials = []

    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        file_len = f.tell()
        f.seek(0)

        while f.tell() < file_len:
            chunk_type = read_u32(f)
            if chunk_type == MDL2_END:
                break
            chunk_size = read_u32(f)
            next_chunk = f.tell() + chunk_size

            if chunk_type in (MDL2_MDL1, MDL2_MDL2):
                f.seek(f.tell() + 12 + 8)
                has_bounding_box = read_u32(f)
                if has_bounding_box:
                    f.seek(f.tell() + 12 + 12 + 12 + 4)
                f.seek(f.tell() + 16 + 48)

                tex_count = read_u32(f)
                tex_start = f.tell()
                textures = []
                for i in range(tex_count):
                    f.seek(tex_start + i * (256 + 8))
                    textures.append(read_string(f, 256))
                f.seek(tex_start + tex_count * (256 + 8))

                mat_count = read_u32(f)
                materials = []
                for i in range(mat_count):
                    if chunk_type == MDL2_MDL2:
                        ambient = read_colour(f)
                        diffuse = read_colour(f)
                        specular = read_colour(f)
                        emissive = read_colour(f)  # noqa: F841
                        shine = read_f32(f)
                        alpha = read_f32(f)
                        alpha_type = read_u32(f)
                        read_u32(f)  # bitfield
                        f.read(8)  # anim_name
                        materials.append(
                            {"ambient": ambient, "diffuse": diffuse, "specular": specular,
                             "shine": shine, "alpha": alpha, "alpha_type": alpha_type}
                        )
                    else:
                        read_u32(f)
                        f.read(4 * 6)
                        materials.append(None)

            elif chunk_type == MDL2_GEO1:
                read_u32(f)  # detail_level_count
                read_u32(f)  # detail_level_type
                read_f32(f)
                render_group_count = read_u32(f)
                f.read(8)

                for _ in range(render_group_count):
                    f.seek(f.tell() + 4)
                    material_id = read_u16(f)
                    f.seek(f.tell() + 2 + 12 + 8)

                    blend_texture_ids = []
                    for _ in range(4):
                        read_u32(f)
                        texture_id = read_u16(f)
                        read_u8(f)
                        read_u8(f)
                        blend_texture_ids.append(texture_id)

                    verts, norms, uvs = _load_mdl2_vertices(f)

                    read_u32(f)
                    fill_type = read_u32(f)
                    idx_count = read_u32(f)
                    indices = [0] * idx_count
                    for i in range(idx_count - 1, -1, -1):
                        indices[i] = read_u16(f)

                    tex_id = blend_texture_ids[0]
                    texture = textures[tex_id] if tex_id < len(textures) else None
                    material = materials[material_id] if material_id < len(materials) else None

                    surfaces.append(
                        {"vertices": verts, "normals": norms, "uvs": uvs, "indices": indices,
                         "fill_type": fill_type, "texture": texture, "material": material}
                    )

            f.seek(next_chunk)

    return surfaces


def triangulate(indices, fill_type):
    tris = []
    if fill_type == 0:
        for i in range(0, len(indices) - 2, 3):
            tris.append((indices[i], indices[i + 1], indices[i + 2]))
    else:
        for i in range(len(indices) - 2):
            a, b, c = indices[i], indices[i + 1], indices[i + 2]
            tris.append((a, b, c) if i % 2 == 0 else (a, c, b))
    return tris


# ==========================================================================
# TDF terrain parsing
# ==========================================================================

TDF_CHUNK_WIDTH = 16
TDF_VERTEX_CHUNK = TDF_CHUNK_WIDTH + 1
TDF_NUM_CHUNKS = 32


def parse_tdf(terrdata_path, terrain_dir=None):
    with open(terrdata_path, "rb") as f:
        f.seek(0x10)
        height_scale = read_f32(f)

        raw_chunks = []
        for i in range(TDF_NUM_CHUNKS * TDF_NUM_CHUNKS):
            f.seek(0x3AE020 + i * 4)
            surface_offset = read_u32(f)

            f.seek(0x366020 + surface_offset + 3 * 4)
            pos_x = read_u16(f)
            pos_y = read_u16(f)

            f.seek(0x366020 + surface_offset + 0x90)
            vertices_offset = read_u32(f)

            f.seek(0x20 + vertices_offset)
            verts = []
            for _ in range(TDF_VERTEX_CHUNK * TDF_VERTEX_CHUNK):
                height = read_u16(f)
                nx = read_i8(f)
                ny = read_i8(f)
                nz = read_i8(f)
                flags = read_u8(f)
                mix_ratios = read_u16(f)
                # 4 nibbles -> 4 blend weights (0.0-1.0), one per texture layer.
                weights = (
                    ((mix_ratios >> 0) & 0xF) / 15.0,
                    ((mix_ratios >> 4) & 0xF) / 15.0,
                    ((mix_ratios >> 8) & 0xF) / 15.0,
                    ((mix_ratios >> 12) & 0xF) / 15.0,
                )
                verts.append((height, nx, ny, nz, flags, weights))

            f.seek(0x366020 + surface_offset + 0x118)
            textures = (read_u8(f), read_u8(f), read_u8(f), read_u8(f))

            raw_chunks.append({"pos_x": pos_x, "pos_y": pos_y, "vertices": verts, "textures": textures})

    half = TDF_CHUNK_WIDTH * TDF_NUM_CHUNKS / 2
    grouped = {}
    for chunk in raw_chunks:
        surf = grouped.setdefault(
            chunk["textures"],
            {"vertices": [], "normals": [], "uvs": [], "cutout": [], "mix_weights": []},
        )
        for sz in range(TDF_VERTEX_CHUNK):
            for sx in range(TDF_VERTEX_CHUNK):
                height, nx, ny, nz, flags, weights = chunk["vertices"][sz * TDF_VERTEX_CHUNK + sx]
                x = chunk["pos_x"] + sx - half
                y = height * height_scale
                z = chunk["pos_y"] + sz - half
                surf["vertices"].append((x, y, z))
                nlen = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
                surf["normals"].append((nx / nlen, ny / nlen, nz / nlen))
                surf["uvs"].append(((chunk["pos_x"] + sx) / TDF_CHUNK_WIDTH, (chunk["pos_y"] + sz) / TDF_CHUNK_WIDTH))
                surf["cutout"].append(bool(flags & 0b10000000))
                surf["mix_weights"].append(weights)

    surfaces = []
    for tex_key, surf in grouped.items():
        verts = surf["vertices"]
        num_local_chunks = len(verts) // (TDF_VERTEX_CHUNK * TDF_VERTEX_CHUNK)
        indices = []
        for c in range(num_local_chunks):
            base_chunk = c * TDF_VERTEX_CHUNK * TDF_VERTEX_CHUNK
            for z in range(TDF_CHUNK_WIDTH):
                for x in range(TDF_CHUNK_WIDTH):
                    base = base_chunk + z * TDF_VERTEX_CHUNK + x
                    if surf["cutout"][base + 1 + TDF_VERTEX_CHUNK]:
                        continue
                    if z % 2 == 0:
                        indices += [base, base + 1, base + TDF_VERTEX_CHUNK,
                                    base + TDF_VERTEX_CHUNK, base + 1, base + 1 + TDF_VERTEX_CHUNK]
                    else:
                        indices += [base, base + 1, base + 1 + TDF_VERTEX_CHUNK,
                                    base, base + 1 + TDF_VERTEX_CHUNK, base + TDF_VERTEX_CHUNK]

        # Resolve up to 4 texture layers for this chunk-group, replicating the
        # original engine's cascading rule: as soon as one slot is 0xFF
        # ("unused"), all subsequent slots are ignored too (see tdf.cpp).
        terrain_tex_paths = [None, None, None, None]
        if terrain_dir is not None:
            for i in range(4):
                if tex_key[i] == 0xFF:
                    break
                candidate = resolve_path_ci(terrain_dir, f"TEXTURE{tex_key[i] + 1}.MIP")
                if candidate is None or not candidate.is_file():
                    break
                terrain_tex_paths[i] = str(candidate)

        # >>> [Kuchenkruste, 11.11.2026] BEGIN of added/changed code: global texture numbers (TEXTURE<n>.MIP, 1-based) of the resolved slots.
        terrain_tex_ids = [tex_key[i] + 1 for i in range(4) if terrain_tex_paths[i]]
        # <<< [Kuchenkruste, 11.11.2026] END of added/changed code.

        texture_label = f"blend_{'_'.join(str(t) for t in tex_key)}"
        if terrain_tex_paths[0]:
            texture_label = terrain_tex_paths[0]

        surfaces.append(
            {
                "vertices": verts, "normals": surf["normals"], "uvs": surf["uvs"], "indices": indices,
                "fill_type": 0, "texture": texture_label, "material": None,
                "mix_weights": surf["mix_weights"], "terrain_tex_paths": terrain_tex_paths,
                # [Kuchenkruste, 11.11.2026] CHANGED: new key "terrain_tex_ids" (used by the experimental terrain options only).
                "terrain_tex_ids": terrain_tex_ids,
            }
        )
    return surfaces


# ==========================================================================
# MIP (TGA) texture decoding
# ==========================================================================


def decode_mip_image(data):
    """Returns (width, height, rgba_bytes) top-down, 8bpc. Raises ValueError
    for unsupported variants (matches original loader's supported subset)."""
    if len(data) < 18:
        raise ValueError("File too small to be a TGA/MIP")

    id_len = data[0]
    cmap_type = data[1]
    img_type = data[2]
    cmap_first = struct.unpack_from("<h", data, 3)[0]
    cmap_len = struct.unpack_from("<H", data, 5)[0]
    cmap_entry_size = data[7]
    width = struct.unpack_from("<H", data, 12)[0]
    height = struct.unpack_from("<H", data, 14)[0]
    pixel_depth = data[16]
    descriptor = data[17]

    if cmap_first != 0:
        raise ValueError(f"Unexpected color map first entry index {cmap_first}")

    pos = 18 + id_len

    if cmap_type == 0:
        if pixel_depth not in (24, 32):
            raise ValueError(f"Unsupported Bit Depth {pixel_depth}")
        bpp = 3 if pixel_depth == 24 else 4
    else:
        if pixel_depth != 8:
            raise ValueError(f"Unsupported Palette Depth {pixel_depth}")
        if cmap_entry_size not in (24, 32):
            raise ValueError(f"Unsupported Bit Depth {cmap_entry_size}")
        bpp = 3 if cmap_entry_size == 24 else 4

    palette = None
    if cmap_type != 0:
        palette_bytes = data[pos:pos + cmap_len * bpp]
        pos += cmap_len * bpp
        palette = [palette_bytes[i * bpp:(i + 1) * bpp] for i in range(cmap_len)]

    if img_type == 1:
        if palette is None:
            raise ValueError("Paletted image type but no colour map present")
        indices = data[pos:pos + width * height]
        pixels_src = [palette[idx] for idx in indices]
    elif img_type == 2:
        raw = data[pos:pos + width * height * bpp]
        pixels_src = [raw[i * bpp:(i + 1) * bpp] for i in range(width * height)]
    else:
        raise ValueError(f"Unsupported Image Type {img_type}")

    vflip_origin_top = bool(descriptor & 0x20)
    hflip_origin_right = bool(descriptor & 0x10)

    out = bytearray(width * height * 4)
    for i in range(height):
        dst_row = i if vflip_origin_top else (height - 1 - i)
        row_base = i * width
        dst_row_base = dst_row * width
        for j in range(width):
            px = pixels_src[row_base + j]
            b, g, r = px[0], px[1], px[2]
            a = px[3] if bpp == 4 else 255
            dst_col = (width - 1 - j) if hflip_origin_right else j
            dst_idx = (dst_row_base + dst_col) * 4
            out[dst_idx:dst_idx + 4] = bytes((r, g, b, a))

    return width, height, bytes(out)


# ==========================================================================
# Case-insensitive path resolution
# ==========================================================================


def resolve_path_ci(root, rel_path):
    rel_path = rel_path.replace("\\", "/").lstrip("/")
    parts = [p for p in rel_path.split("/") if p not in ("", ".")]
    current = Path(root)
    for part in parts:
        if not current.is_dir():
            return None
        match = None
        try:
            for entry in current.iterdir():
                if entry.name.lower() == part.lower():
                    match = entry
                    break
        except OSError:
            return None
        if match is None:
            return None
        current = match
    return current


# >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: IFL image-sequence resolution for Sea textures.
# ==========================================================================
# IFL (image sequence) resolution
# ==========================================================================


def resolve_first_ifl_texture(ifl_path, gamedata_root):
    """Resolve an LR2 .ifl to its first image frame.

    IFL files are simple image-sequence lists. For Blender we only need the
    first frame, and the game's image files normally live beside the IFL.
    Returns a resolved Path or None.
    """
    if ifl_path is None:
        return None
    path_text = str(ifl_path).strip()
    if not path_text:
        return None

    if os.path.isabs(path_text):
        ifl_file = Path(path_text)
    else:
        ifl_file = None
        if gamedata_root:
            # WRL strings commonly include the literal "game data\\" prefix,
            # while gamedata_root already points to that directory.
            normalized = path_text.replace("\\", "/").lstrip("/")
            parts = [p for p in normalized.split("/") if p not in ("", ".")]
            if parts and parts[0].lower() == Path(gamedata_root).name.lower():
                normalized = "/".join(parts[1:])
            elif parts and parts[0].lower() == "game data":
                normalized = "/".join(parts[1:])

            if normalized:
                ifl_file = resolve_path_ci(gamedata_root, normalized)

            # Also try the literal path in case the supplied root is actually
            # the install root rather than GAME DATA.
            if ifl_file is None:
                ifl_file = resolve_path_ci(gamedata_root, path_text)

    if ifl_file is None or not ifl_file.is_file():
        return None

    try:
        raw = ifl_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    for line in raw.splitlines():
        frame = line.strip().lstrip("\ufeff").strip().strip('"').strip()
        if not frame or frame.startswith(";") or frame.startswith("#"):
            continue

        # LR2 IFL entries are "filename duration". The actual Sandy file,
        # for example, contains "water01.tga 3". Keep only the filename.
        parts = frame.split()
        frame = parts[0].strip('"') if parts else ""
        if not frame:
            continue

        # IFL names describe the logical image format (e.g. water01.tga),
        # but LR2's actual on-disk texture is an MIP that our existing MIP
        # decoder must parse. Therefore water01.tga -> water01.mip / water01.
        frame_path = Path(frame)
        frame_stem = frame_path.with_suffix("") if frame_path.suffix else frame_path
        frame_candidates = [frame]
        frame_candidates += [
            str(frame_stem) + ext
            for ext in (".mip", ".MIP", "")
        ]

        for candidate in frame_candidates:
            resolved = resolve_path_ci(ifl_file.parent, candidate)
            if resolved is not None and resolved.is_file():
                print(
                    f"[LR2 Import] Sea IFL: {ifl_file.name} -> "
                    f"{resolved.name}"
                )
                return resolved

        # Fall back to Game Data-relative paths.
        if gamedata_root:
            for candidate in frame_candidates:
                resolved = resolve_path_ci(gamedata_root, candidate)
                if resolved is not None and resolved.is_file():
                    print(
                        f"[LR2 Import] Sea IFL: {ifl_file.name} -> "
                        f"{resolved.name}"
                    )
                    return resolved
    return None


def resolve_sea_texture(texture_path, gamedata_root):
    """Resolve a cSea texture, using the first frame when it is an IFL."""
    if not texture_path:
        return None

    path_text = str(texture_path).strip()
    if path_text.lower().endswith(".ifl"):
        first = resolve_first_ifl_texture(path_text, gamedata_root)
        if first is not None:
            return first
        print(f"[LR2 Import] Sea IFL has no resolvable first frame: {path_text}")
        return None

    if os.path.isabs(path_text) and Path(path_text).is_file():
        return Path(path_text)

    if gamedata_root:
        normalized = path_text.replace("\\", "/").lstrip("/")
        parts = [p for p in normalized.split("/") if p not in ("", ".")]
        if parts and (
            parts[0].lower() == Path(gamedata_root).name.lower()
            or parts[0].lower() == "game data"
        ):
            normalized = "/".join(parts[1:])

        candidates = [normalized or path_text]
        stem = candidates[0].rsplit(".", 1)[0] if "." in candidates[0] else candidates[0]
        candidates += [stem + ".mip", stem + ".MIP"]
        for cand in candidates:
            resolved = resolve_path_ci(gamedata_root, cand)
            if resolved is not None and resolved.is_file():
                return resolved
    return None


# <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: IFL resolution.
# ==========================================================================
# Blender-side construction
# ==========================================================================


def build_image(name, width, height, rgba_top_down):
    """Create a bpy.types.Image from top-down RGBA bytes. Always creates a
    fresh image block (Blender will auto-suffix the name with .001 etc. if
    one already exists from a previous import run) - deduplication within a
    single import is handled by the caller's image_cache, not by reusing
    stale same-named images left over from earlier runs/sessions."""
    img = bpy.data.images.new(name, width=width, height=height, alpha=True)
    # Blender's pixel buffer is bottom-up (row 0 = bottom); our decoded data
    # is top-down, so reverse row order. Values must be normalised floats.
    stride = width * 4
    flipped = bytearray(len(rgba_top_down))
    for row in range(height):
        src = row * stride
        dst = (height - 1 - row) * stride
        flipped[dst:dst + stride] = rgba_top_down[src:src + stride]
    img.pixels = [c / 255.0 for c in flipped]
    img.pack()
    return img


def load_image_cached(image_cache, resolved_path):
    """resolved_path: pathlib.Path, already confirmed to exist. Cached by
    absolute path so the same texture used across many materials (very
    common for terrain) is only decoded/uploaded once."""
    key = str(resolved_path)
    if key in image_cache:
        return image_cache[key]
    img = None
    try:
        width, height, rgba = decode_mip_image(resolved_path.read_bytes())
        img = build_image(resolved_path.stem[:56], width, height, rgba)
    except (ValueError, OSError, IndexError, struct.error) as e:
        print(f"[LR2 Import] Could not decode texture {resolved_path}: {e}")
    image_cache[key] = img
    return img


def new_separate_rgb_node(nodes):
    """Blender 3.3+ renamed 'Separate RGB' to 'Separate Color'; support both."""
    try:
        n = nodes.new("ShaderNodeSeparateColor")
        n.mode = "RGB"
        return n, ("Red", "Green", "Blue")
    except RuntimeError:
        n = nodes.new("ShaderNodeSeparateRGB")
        return n, ("R", "G", "B")


def set_alpha_blend(mat, alpha_type=None):
    """Configure transparency to match the original engine's alpha_type
    (see mdl2.cpp): 0 = scissor/cutout (hard edge, threshold 0.5 - NOT soft
    translucency), 1 = real alpha blending, 4 = additive. Falls back to
    scissor/cutout for unknown/missing alpha_type, since that avoids
    introducing unwanted translucency on materials that were never meant
    to be see-through."""
    if alpha_type == 1:
        method = "BLEND"
    elif alpha_type == 4:
        method = "BLEND"  # closest available approximation of additive
    else:  # 0, or unknown - original engine treats unknown as an error case
        method = "CLIP"

    for attr, value in (("blend_method", method), ("shadow_method", method)):
        if hasattr(mat, attr):
            try:
                setattr(mat, attr, value)
            except TypeError:
                pass  # enum value not valid on this Blender version, skip

    if method == "CLIP" and hasattr(mat, "alpha_threshold"):
        mat.alpha_threshold = 0.5


def get_or_build_material(cache, image_cache, texture_path, material_props, gamedata_root):
    """texture_path may be a logical '.tga' name (resolved under gamedata_root,
    trying a .mip fallback) or an already-absolute resolved path (terrain)."""
    diffuse = tuple(material_props["diffuse"][:3]) if material_props else None
    key = (texture_path, diffuse)
    if key in cache:
        return cache[key]

    mat_name = f"LR2_mat_{len(cache)}"
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")

    if material_props:
        r, g, b = material_props["diffuse"][:3]
        a = max(0.0, min(1.0, 1.0 - material_props["alpha"]))
        alpha_type = material_props.get("alpha_type")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
            if "Alpha" in bsdf.inputs:
                bsdf.inputs["Alpha"].default_value = a
        if a < 0.999:
            set_alpha_blend(mat, alpha_type)

    resolved = None
    if gamedata_root and texture_path:
        if os.path.isabs(texture_path) and Path(texture_path).is_file():
            resolved = Path(texture_path)
        else:
            candidates = [texture_path]
            stem = texture_path.rsplit(".", 1)[0] if "." in texture_path else texture_path
            candidates += [stem + ".mip", stem + ".MIP"]
            for cand in candidates:
                r_path = resolve_path_ci(gamedata_root, cand)
                if r_path is not None and r_path.is_file():
                    resolved = r_path
                    break

    if resolved is not None:
        img = load_image_cached(image_cache, resolved)
        if img is not None and bsdf:
            tex_node = nodes.new("ShaderNodeTexImage")
            tex_node.image = img
            tex_node.location = (bsdf.location.x - 300, bsdf.location.y)
            links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
            if "Alpha" in bsdf.inputs:
                links.new(tex_node.outputs["Alpha"], bsdf.inputs["Alpha"])
                set_alpha_blend(mat, material_props.get("alpha_type") if material_props else None)

    cache[key] = mat
    return mat


TERRAIN_MIX_LAYER_NAME = "TerrainMix"


def build_terrain_material(cache, image_cache, tex_paths):
    """Blends up to 4 textures using per-vertex weights stored in a
    TerrainMix vertex-color layer (R=w0, G=w1, B=w2, A=w3), replicating the
    original engine's terrain shader (see tdf.cpp): ALBEDO = sum(tex_i * w_i).
    """
    key = ("terrain", tuple(tex_paths))
    if key in cache:
        return cache[key]

    mat = bpy.data.materials.new(f"LR2_terrain_mat_{len(cache)}")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")

    attr_node = nodes.new("ShaderNodeVertexColor")
    attr_node.layer_name = TERRAIN_MIX_LAYER_NAME
    attr_node.location = (-900, 300)

    sep_node, comp_names = new_separate_rgb_node(nodes)
    sep_node.location = (-700, 300)
    links.new(attr_node.outputs["Color"], sep_node.inputs[0])
    weight_outputs = [
        sep_node.outputs[comp_names[0]],
        sep_node.outputs[comp_names[1]],
        sep_node.outputs[comp_names[2]],
        attr_node.outputs["Alpha"],
    ]

    sum_socket = None
    y = 0
    for i, path_str in enumerate(tex_paths):
        if not path_str:
            break
        img = load_image_cached(image_cache, Path(path_str))
        if img is None:
            break

        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.image = img
        tex_node.location = (-500, y)

        scale_node = nodes.new("ShaderNodeVectorMath")
        scale_node.operation = "SCALE"
        scale_node.location = (-250, y)
        links.new(tex_node.outputs["Color"], scale_node.inputs[0])
        links.new(weight_outputs[i], scale_node.inputs["Scale"])

        if sum_socket is None:
            sum_socket = scale_node.outputs[0]
        else:
            add_node = nodes.new("ShaderNodeVectorMath")
            add_node.operation = "ADD"
            add_node.location = (0, y)
            links.new(sum_socket, add_node.inputs[0])
            links.new(scale_node.outputs[0], add_node.inputs[1])
            sum_socket = add_node.outputs[0]
        y -= 260

    # Deliberately NOT touching alpha here: the original terrain shader
    # discards it entirely (`ALBEDO = (...).rgb`) and always renders fully
    # opaque (depth_draw_opaque, no blend_mix). Terrain textures happen to
    # be 32bpp and thus carry an (unused-by-the-engine) alpha channel; wiring
    # it up here would incorrectly cut out texture-blend transition areas.
    if sum_socket is not None and bsdf:
        links.new(sum_socket, bsdf.inputs["Base Color"])

    cache[key] = mat
    return mat


# >>> [Kuchenkruste, 11.11.2026] BEGIN of added/changed code: experimental terrain helpers (unified material, collision copy).
TERRAIN_WEIGHT_PREFIX = "TerrainW_"
COLLISION_MATERIAL_PREFIX = "COL_TEXTURE"


def terrain_weight_attr_name(tex_id):
    return f"{TERRAIN_WEIGHT_PREFIX}{tex_id}"


def collect_terrain_textures(surfaces):
    """Returns {texture number: absolute path} for all textures used by the
    given terrain surfaces (texture number = n in TEXTURE<n>.MIP)."""
    result = {}
    for surf in surfaces:
        ids = surf.get("terrain_tex_ids") or []
        paths = [p for p in (surf.get("terrain_tex_paths") or []) if p]
        for tex_id, path in zip(ids, paths):
            result.setdefault(tex_id, path)
    return result


def build_unified_terrain_material(cache, image_cache, tex_paths_by_id):
    """One material for the whole terrain: every terrain texture gets one
    Image Texture node (named TEXTURE<n>) weighted by a per-vertex float
    attribute (TerrainW_<n>); ALBEDO = sum(tex_n * w_n), like the original
    engine's terrain shader. Re-texturing = swap the image in one node."""
    key = ("terrain_unified", tuple(sorted(tex_paths_by_id.items())))
    if key in cache:
        return cache[key]

    mat = bpy.data.materials.new("LR2_terrain_unified")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")

    sum_socket = None
    y = 0
    for tex_id in sorted(tex_paths_by_id):
        img = load_image_cached(image_cache, Path(tex_paths_by_id[tex_id]))
        if img is None:
            continue

        attr_node = nodes.new("ShaderNodeAttribute")
        attr_node.attribute_type = "GEOMETRY"
        attr_node.attribute_name = terrain_weight_attr_name(tex_id)
        attr_node.location = (-700, y)

        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.image = img
        tex_node.name = f"TEXTURE{tex_id}"
        tex_node.label = f"TEXTURE{tex_id}"
        tex_node.location = (-500, y)

        scale_node = nodes.new("ShaderNodeVectorMath")
        scale_node.operation = "SCALE"
        scale_node.location = (-250, y)
        links.new(tex_node.outputs["Color"], scale_node.inputs[0])
        links.new(attr_node.outputs["Fac"], scale_node.inputs["Scale"])

        if sum_socket is None:
            sum_socket = scale_node.outputs[0]
        else:
            add_node = nodes.new("ShaderNodeVectorMath")
            add_node.operation = "ADD"
            add_node.location = (0, y)
            links.new(sum_socket, add_node.inputs[0])
            links.new(scale_node.outputs[0], add_node.inputs[1])
            sum_socket = add_node.outputs[0]
        y -= 260

    # Alpha deliberately ignored, same reasoning as build_terrain_material().
    if sum_socket is not None and bsdf:
        links.new(sum_socket, bsdf.inputs["Base Color"])

    cache[key] = mat
    return mat


def get_collision_material(cache, tex_id):
    """Flat-colour material COL_TEXTURE<n> (one per terrain texture). Reused
    by name so renamed/tuned materials survive re-imports."""
    key = ("terrain_col", tex_id)
    if key in cache:
        return cache[key]
    name = f"{COLLISION_MATERIAL_PREFIX}{tex_id}"
    mat = bpy.data.materials.get(name)
    if mat is None:
        import colorsys
        r, g, b = colorsys.hsv_to_rgb((tex_id * 0.61803398875) % 1.0, 0.65, 0.9)
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = (r, g, b, 1.0)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
    mat["lr2_texture_id"] = tex_id
    cache[key] = mat
    return mat


def build_terrain_collision_object(name, surfaces, convert_axes, material_cache):
    """Geometry-only copy of the terrain where every triangle gets the
    COL_TEXTURE<n> material of its dominant texture (largest summed blend
    weight over the triangle's three vertices; ties -> lower texture number)."""
    all_ids = sorted(collect_terrain_textures(surfaces))
    if not all_ids:
        return None

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    mats = [get_collision_material(material_cache, t) for t in all_ids]
    slot_of = {t: i for i, t in enumerate(all_ids)}
    skipped = 0

    for surf in surfaces:
        verts_src = surf["vertices"]
        weights_src = surf.get("mix_weights")
        ids = surf.get("terrain_tex_ids") or []
        tris = triangulate(surf["indices"], surf["fill_type"])
        if not verts_src or not tris:
            continue
        if not ids or not weights_src:
            skipped += len(tris)
            continue

        bm_verts = [bm.verts.new(convert_position(v) if convert_axes else v) for v in verts_src]
        for a, b, c in tris:
            try:
                face = bm.faces.new((bm_verts[a], bm_verts[b], bm_verts[c]))
            except ValueError:
                continue
            sums = [0.0] * len(ids)
            for idx in (a, b, c):
                w = weights_src[idx]
                for s in range(len(ids)):
                    sums[s] += w[s]
            # Ties resolve to the lower texture number (independent of the chunk's slot order).
            best = max(range(len(ids)), key=lambda s: (sums[s], -ids[s]))
            face.material_index = slot_of[ids[best]]

    # Drop vertices of cut-out (hole) quads, then weld the duplicated chunk-border vertices.
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    bm.to_mesh(mesh)
    bm.free()
    for mat in mats:
        mesh.materials.append(mat)
    if skipped:
        log(f"[LR2 Import] Terrain collision: {skipped} triangles skipped (no resolvable texture).")

    obj = bpy.data.objects.new(name, mesh)
    obj["lr2_collision_source"] = name
    return obj
# <<< [Kuchenkruste, 11.11.2026] END of added/changed code: experimental terrain helpers.


# >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: build_sea (cSea), WaterCourse Bezier/grid construction (build_watercourse) and build_water_sheet (cWaterSheet).
SEA_DEFAULT_PLANE_SIZE = 10000.0


def build_sea(name, size, height, convert_axes, material_cache, image_cache,
              texture_path, rgb_s, gamedata_root, texture_tiles=20.0):
    """Build cSea as a large horizontal plane.

    cSea Hght is used as a raw source-world height baseline and converted to Blender Z.
    If the texture path is an .ifl, only its first frame is used.
    """
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()

    half = float(size) * 0.5
    source_verts = [
        (-half, 0.0, -half),
        (-half, 0.0, half),
        (half, 0.0, half),
        (half, 0.0, -half),
    ]
    verts = [convert_position(v) if convert_axes else v for v in source_verts]
    bm_verts = [bm.verts.new(v) for v in verts]

    # Source +Y is the upward normal.
    face = bm.faces.new((bm_verts[0], bm_verts[3], bm_verts[2], bm_verts[1]))
    uv_layer = bm.loops.layers.uv.new()
    # Increase Sea texture density by 50% while keeping physical Sea size
    # independent from its UV tiling.
    tiles = max(1.0, float(texture_tiles)) * 1.5
    for loop, uv in zip(
        face.loops,
        ((0.0, 0.0), (tiles, 0.0), (tiles, tiles), (0.0, tiles)),
    ):
        loop[uv_layer].uv = uv

    bm.to_mesh(mesh)
    bm.free()

    tint = tuple(rgb_s) if rgb_s and len(rgb_s) >= 4 else (255, 255, 255, 0)
    r, g, b, strength = [max(0, min(255, int(v))) for v in tint]
    tint_rgba = (r / 255.0, g / 255.0, b / 255.0, 1.0)

    key = ("sea", texture_path, tuple(tint))
    mat = material_cache.get(key)
    if mat is None:
        mat = bpy.data.materials.new(f"LR2_sea_mat_{len(material_cache)}")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        bsdf = nodes.get("Principled BSDF")

        if bsdf:
            bsdf.inputs["Base Color"].default_value = tint_rgba

        resolved = resolve_sea_texture(texture_path, gamedata_root)
        print(
            f"[LR2 Import] Sea texture: source={texture_path!r}, "
            f"resolved={str(resolved) if resolved else None!r}"
        )
        if resolved is not None and bsdf:
            img = load_image_cached(image_cache, resolved)
            if img is None:
                print(f"[LR2 Import] Sea texture decode failed: {resolved}")
            if img is not None:
                tex_node = nodes.new("ShaderNodeTexImage")
                tex_node.image = img
                tex_node.extension = "REPEAT"
                tex_node.location = (-500, 0)

                mix = nodes.new("ShaderNodeMixRGB")
                mix.blend_type = "MULTIPLY"
                # Fourth RGBS byte is intentionally uninterpreted.
                mix.inputs[0].default_value = 1.0
                mix.inputs[2].default_value = tint_rgba
                mix.location = (-180, 0)
                links.new(tex_node.outputs["Color"], mix.inputs[1])
                links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])

        material_cache[key] = mat

    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    # cSea Hght is applied as an absolute source-world height after creation.
    obj.location = (0.0, 0.0, 0.0)
    return obj




# WaterCourse geometry reconstruction based on recovered game code.
#
# The game evaluates the control-point positions as a full Bezier curve using
# Bernstein polynomials, then samples a procedural grid along that curve.  The
# stored GridResolution is converted to a power-of-two grid size:
#     grid = (1 << (GridResolution + 2)) + 1
# The original engine also has a runtime LOD adjustment; the Blender importer
# deliberately uses the WRL's stored resolution directly so imported geometry
# is deterministic.
#
# The exact renderer's material animation is separate from the geometry.  For
# Blender we retain a small flow-UV calibration knob so visual matching can be
# tuned without changing the recovered mesh construction.
# MatProps texture-tiling calibration recovered experimentally from the game.
# DINO stores 100.0 at MatProps+0x40; a 0.1 multiplier gives the observed
# longitudinal repetition. Crucially, the source field is per WaterCourse,
# so worlds with different values (e.g. Mars) do not share one hard-coded tile
# density.
# Independent longitudinal calibration.  U is already empirically landing at
# roughly 4-4.5 repeats across Dino/Mars; V needs more repeats to avoid the
# obvious lengthwise squashing seen in the imported courses.
# Final longitudinal calibration knob. 1.0 preserves the current V14 scale.
WATERCOURSE_LENGTH_SCALE = 1.0


def _binomial(n, k):
    if k < 0 or k > n:
        return 0
    # Small n (max 31 control points) makes this cheap and stable.
    return math.comb(n, k)


def _bezier_eval(points, t):
    """Evaluate XYZ + VOffset using the game's Bernstein/Bezier scheme."""
    count = len(points)
    if count == 1:
        p = points[0]
        return tuple(p["position"]), float(p.get("v_offset", 0.0) or 0.0)

    n = count - 1
    omt = 1.0 - t
    x = y = z = vo = 0.0
    for i, p in enumerate(points):
        w = _binomial(n, i) * (omt ** (n - i)) * (t ** i)
        px, py, pz = p["position"]
        x += px * w
        y += py * w
        z += pz * w
        vo += float(p.get("v_offset", 0.0) or 0.0) * w
    return (x, y, z), vo


def _bezier_tangent(points, t):
    """Analytic derivative of the Bezier centerline."""
    count = len(points)
    if count < 2:
        return (1.0, 0.0, 0.0)

    n = count - 1
    omt = 1.0 - t
    tx = ty = tz = 0.0
    for i in range(n):
        w = _binomial(n - 1, i) * (omt ** (n - 1 - i)) * (t ** i)
        p0 = points[i]["position"]
        p1 = points[i + 1]["position"]
        tx += (p1[0] - p0[0]) * w
        ty += (p1[1] - p0[1]) * w
        tz += (p1[2] - p0[2]) * w

    tx *= n
    ty *= n
    tz *= n
    length = math.sqrt(tx * tx + ty * ty + tz * tz)
    if length <= 1e-10:
        # Find a usable local direction if a degenerate tangent occurs.
        for i in range(count - 1):
            p0 = points[i]["position"]
            p1 = points[i + 1]["position"]
            dx, dy, dz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length > 1e-10:
                return (dx / length, dy / length, dz / length)
        return (1.0, 0.0, 0.0)
    return (tx / length, ty / length, tz / length)


def _watercourse_width(points, t):
    """Interpolate Width along the control-point parameter linearly.

    The recovered Bezier helper visibly evaluates Position + VOffset (four
    values), while Width is a separate fifth value, so we keep width as a
    piecewise-linear field rather than falsely treating it as a Bezier value.
    """
    if len(points) == 1:
        return abs(float(points[0].get("width", 0.0) or 0.0))
    s = max(0.0, min(1.0, t)) * (len(points) - 1)
    i = min(int(math.floor(s)), len(points) - 2)
    a = s - i
    w0 = float(points[i].get("width", 0.0) or 0.0)
    w1 = float(points[i + 1].get("width", 0.0) or 0.0)
    return abs(w0 + (w1 - w0) * a)


def _watercourse_side_from_tangent(tangent):
    """Return the horizontal cross-course direction in LR2 source space."""
    tx, ty, tz = tangent
    # LR2 world data is Y-up.  Project tangent into X/Z and take a 90-degree
    # horizontal perpendicular. This keeps the generated course surface flat
    # across its width while still following the 3D Bezier centerline height.
    length = math.sqrt(tx * tx + tz * tz)
    if length > 1e-10:
        sx, sz = tz / length, -tx / length
        return (sx, 0.0, sz)
    # Nearly vertical tangent: choose a stable fallback.
    return (1.0, 0.0, 0.0)


def _watercourse_grid_resolution(stored_resolution):
    try:
        r = int(stored_resolution)
    except (TypeError, ValueError):
        r = 1
    r = max(0, min(r, 5))
    return (1 << (r + 2)) + 1


def build_watercourse(name, points, course_ref, grid_resolution,
                      bottom_layer_speed, top_layer_speed, wavyness,
                      texture_disturbance, flow_direction,
                      texture_tiling, texture_tiling_aux,
                      flow_uv_factor, longitudinal_uv_factor,
                      convert_axes, material_cache, image_cache, water_texture,
                      bed_texture, gamedata_root):
    """Generate a procedural cWaterCourse mesh from the recovered data model."""
    if len(points) < 2:
        raise ValueError("WaterCourse needs at least two control points")

    ref = tuple(course_ref or (0.0, 0.0, 0.0))
    grid = _watercourse_grid_resolution(grid_resolution)

    # Sample the exact Bezier centerline at the same number of longitudinal
    # rows as the generated grid dimension.
    samples = []
    for i in range(grid):
        t = i / float(grid - 1)
        center, v_offset = _bezier_eval(points, t)
        tangent = _bezier_tangent(points, t)
        width = _watercourse_width(points, t)
        side = _watercourse_side_from_tangent(tangent)
        samples.append((
            (center[0] + ref[0], center[1] + ref[1], center[2] + ref[2]),
            width,
            v_offset,
            side,
        ))

    # Longitudinal UVs repeat once per control-point segment.
    try:
        texture_tiles = float(texture_tiling)
    except (TypeError, ValueError):
        texture_tiles = 1.0
    if not math.isfinite(texture_tiles) or texture_tiles <= 0.0:
        texture_tiles = 1.0
    effective_flow_tiles = 1.0

    mesh = bpy.data.meshes.new(name)
    verts = []
    faces = []
    uvs = []

    # Grid columns run across the width. Rows run along the Bezier course.
    for row, (center, width, v_offset, side) in enumerate(samples):
        sx, sy, sz = side
        half = width * 0.5
        segment_count = max(1, len(points) - 1)
        v_base = (row / float(grid - 1)) * segment_count * WATERCOURSE_LENGTH_SCALE + v_offset

        for col in range(grid):
            across = -1.0 + 2.0 * (col / float(grid - 1))
            px = center[0] + sx * half * across
            py = center[1]
            pz = center[2] + sz * half * across
            src = (px, py, pz)
            verts.append(convert_position(src) if convert_axes else src)

            # U is across the course.  Keep its texture density in WORLD
            # SPACE instead of normalizing it by the total course length.  V's
            # texture_tiling value describes longitudinal repetition; using
            # total_distance here made U depend on how long the whole river
            # happened to be, which caused wide Mars courses to be under-tiled
            # and also changed Dino's width appearance.
            #
            # The recovered 0.1 UV calibration gives a useful world-space
            # repeat density across the width: a 10-unit-wide course gets about
            # one texture repeat, a 20-unit-wide course gets about two, etc.
            u = col / float(grid - 1)
            uvs.append((u, v_base))

    # Triangle indices for the NxN grid.  Using ordinary triangles here is
    # Blender-friendly; the original engine uses a strip-oriented index buffer
    # internally, but the vertex topology is the same grid.
    for row in range(grid - 1):
        base0 = row * grid
        base1 = (row + 1) * grid
        for col in range(grid - 1):
            a = base0 + col
            b = base0 + col + 1
            c = base1 + col + 1
            d = base1 + col
            faces.append((a, b, c, d))

    mesh.from_pydata(verts, [], faces)
    mesh.update()

    uv_layer = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            vi = mesh.loops[loop_index].vertex_index
            uv_layer.data[loop_index].uv = uvs[vi]

    # WaterCourse is a two-layer material: BottomTexture + TopTexture.
    # The top texture's alpha is used as the layer mask when available.  This
    # preserves the old V6 appearance for opaque top textures while allowing
    # Mars-style courses to expose the underlying bottom/lava texture.
    key = ("watercourse", water_texture, bed_texture,
           round(float(bottom_layer_speed), 6), round(float(top_layer_speed), 6),
           round(float(wavyness), 6), round(float(texture_disturbance), 6),
           int(flow_direction), round(float(texture_tiles), 6))
    mat = material_cache.get(key)
    if mat is None:
        mat = bpy.data.materials.new(f"LR2_watercourse_mat_{len(material_cache)}")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        bsdf = nodes.get("Principled BSDF")

        def resolve_texture(tex_path):
            if not tex_path or not gamedata_root:
                return None
            candidates = [tex_path]
            stem = tex_path.rsplit(".", 1)[0] if "." in tex_path else tex_path
            candidates += [stem + ".mip", stem + ".MIP"]
            for cand in candidates:
                rp = resolve_path_ci(gamedata_root, cand)
                if rp is not None and rp.is_file():
                    return rp
            return None

        bottom_resolved = resolve_texture(bed_texture)
        top_resolved = resolve_texture(water_texture)

        if bsdf is not None:
            # Separate UV mapping nodes make the two runtime layers independent.
            # The actual game scrolls these using BottomLayerSpeed/TopLayerSpeed;
            # for the Blender preview we keep frame-0 coordinates stable and
            # expose the speeds as custom properties below.
            uv = nodes.new("ShaderNodeUVMap")
            uv.location = (-900, 0)
            uv.uv_map = "UVMap"

            bottom_tex = None
            if bottom_resolved is not None:
                bottom_tex = nodes.new("ShaderNodeTexImage")
                bottom_tex.image = load_image_cached(image_cache, bottom_resolved)
                bottom_tex.extension = "REPEAT"
                bottom_tex.location = (-620, -120)

            top_tex = None
            if top_resolved is not None:
                top_tex = nodes.new("ShaderNodeTexImage")
                top_tex.image = load_image_cached(image_cache, top_resolved)
                top_tex.extension = "REPEAT"
                top_tex.location = (-620, 140)

            if bottom_tex is not None and top_tex is not None:
                links.new(uv.outputs["UV"], bottom_tex.inputs["Vector"])
                links.new(uv.outputs["UV"], top_tex.inputs["Vector"])

                # Prefer the top layer wherever it supplies alpha.  For fully
                # opaque top textures this intentionally reduces to the V6
                # result, so DINO remains unchanged while transparent/partial
                # top layers reveal their bottom layer (important for Mars).
                mix = nodes.new("ShaderNodeMixRGB")
                mix.blend_type = "MIX"
                mix.inputs[0].default_value = 1.0
                mix.location = (-80, 60)
                links.new(top_tex.outputs["Alpha"], mix.inputs[0])
                links.new(bottom_tex.outputs["Color"], mix.inputs[1])
                links.new(top_tex.outputs["Color"], mix.inputs[2])
                links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])
            elif top_tex is not None:
                links.new(uv.outputs["UV"], top_tex.inputs["Vector"])
                links.new(top_tex.outputs["Color"], bsdf.inputs["Base Color"])
            elif bottom_tex is not None:
                links.new(uv.outputs["UV"], bottom_tex.inputs["Vector"])
                links.new(bottom_tex.outputs["Color"], bsdf.inputs["Base Color"])

            if "Roughness" in bsdf.inputs:
                bsdf.inputs["Roughness"].default_value = 0.25

        material_cache[key] = mat

    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    obj["lr2_object_type"] = "cWaterCourse"
    obj["lr2_watercourse_grid_resolution"] = int(grid_resolution)
    obj["lr2_watercourse_grid_size"] = grid
    obj["lr2_watercourse_bottom_layer_speed"] = float(bottom_layer_speed)
    obj["lr2_watercourse_top_layer_speed"] = float(top_layer_speed)
    obj["lr2_watercourse_wavyness"] = float(wavyness)
    obj["lr2_watercourse_texture_disturbance"] = float(texture_disturbance)
    obj["lr2_watercourse_flow_direction"] = int(flow_direction)
    obj["lr2_watercourse_uv_flow_factor"] = 1.0
    obj["lr2_watercourse_uv_width_factor"] = float(flow_uv_factor)
    obj["lr2_watercourse_uv_longitudinal_factor"] = float(longitudinal_uv_factor)
    obj["lr2_watercourse_texture_tiling"] = texture_tiles
    obj["lr2_watercourse_texture_tiling_aux"] = float(texture_tiling_aux) if texture_tiling_aux is not None else 0.0
    obj["lr2_watercourse_effective_flow_tiles"] = effective_flow_tiles
    obj["lr2_watercourse_uv_width_mode"] = "physical_width_scaled"
    obj["lr2_watercourse_water_texture"] = water_texture
    obj["lr2_watercourse_bed_texture"] = bed_texture
    obj["lr2_watercourse_point_count"] = len(points)
    obj["lr2_watercourse_course_ref"] = ref
    return obj

def build_water_sheet(name, width, height, convert_axes, material_cache, image_cache,
                      water_texture, reflection_texture, gamedata_root):
    """Build a horizontal WaterSheet plane in the source LR2 coordinate system.

    WaterSheet records contain their geometry dimensions directly in the WRL;
    unlike MD2 objects, there is no external model to load.
    """
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()

    # Source plane lies in LR2 X/Z with +Y as its upward normal.
    hx = width * 0.5
    hz = height * 0.5
    source_verts = [
        (-hx, 0.0, -hz),
        (-hx, 0.0,  hz),
        ( hx, 0.0,  hz),
        ( hx, 0.0, -hz),
    ]
    verts = [convert_position(v) if convert_axes else v for v in source_verts]
    bm_verts = [bm.verts.new(v) for v in verts]

    # +Y source normal: choose winding accordingly.
    face = bm.faces.new((bm_verts[0], bm_verts[3], bm_verts[2], bm_verts[1]))

    uv_layer = bm.loops.layers.uv.new()
    for loop, uv in zip(face.loops, ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))):
        loop[uv_layer].uv = uv

    bm.to_mesh(mesh)
    bm.free()

    # Use the same material/texture resolution path as ordinary models.
    mat = get_or_build_material(
        material_cache, image_cache, water_texture, None, gamedata_root
    )
    mesh.materials.append(mat)

    obj = bpy.data.objects.new(name, mesh)
    return obj


# <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: build_sea / build_watercourse / build_water_sheet.
# [Kuchenkruste, 11.11.2026] CHANGED: new parameter terrain_unified (experimental unified terrain material).
def build_object(name, surfaces, convert_axes, material_cache, image_cache, gamedata_root,
                  is_terrain=False, uv_scale=(1.0, 1.0), terrain_unified=False):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new()
    # [Kuchenkruste, 11.11.2026] CHANGED: the legacy TerrainMix layer is not created in unified mode; per-texture float weight layers are created instead.
    color_layer = bm.loops.layers.color.new(TERRAIN_MIX_LAYER_NAME) if is_terrain and not terrain_unified else None
    unified_paths = collect_terrain_textures(surfaces) if is_terrain and terrain_unified else {}
    weight_layers = {t: bm.verts.layers.float.new(terrain_weight_attr_name(t)) for t in sorted(unified_paths)}

    all_normals = []  # per-mesh-vertex, filled in bm-vertex creation order
    face_material_pairs = []  # (bm.face, material) to assign after materials built

    mat_slots = []  # ordered list of unique materials for this object
    mat_slot_index = {}

    for surf in surfaces:
        verts_src = surf["vertices"]
        norms_src = surf["normals"]
        uvs_src = surf["uvs"]
        weights_src = surf.get("mix_weights") if is_terrain else None
        tris = triangulate(surf["indices"], surf["fill_type"])
        if not verts_src or not tris:
            continue

        bm_verts = []
        for i, v in enumerate(verts_src):
            vv = convert_position(v) if convert_axes else v
            bv = bm.verts.new(vv)
            bm_verts.append(bv)
            n = norms_src[i] if i < len(norms_src) and norms_src[i] else (0.0, 1.0, 0.0)
            nn = convert_direction(n) if convert_axes else n
            all_normals.append(nn)

        # >>> [Kuchenkruste, 11.11.2026] BEGIN of added/changed code: per-vertex weights per global texture (unified mode).
        if weight_layers:
            ids = surf.get("terrain_tex_ids") or []
            for i, bv in enumerate(bm_verts):
                if i < len(weights_src):
                    for slot, tex_id in enumerate(ids):
                        bv[weight_layers[tex_id]] = weights_src[i][slot]
        # <<< [Kuchenkruste, 11.11.2026] END of added/changed code.

        if is_terrain and terrain_unified:
            mat = build_unified_terrain_material(material_cache, image_cache, unified_paths)
        elif is_terrain:
            mat = build_terrain_material(material_cache, image_cache, surf.get("terrain_tex_paths", []))
        else:
            mat = get_or_build_material(
                material_cache, image_cache, surf.get("texture"), surf.get("material"), gamedata_root
            )
        if id(mat) not in mat_slot_index:
            mat_slot_index[id(mat)] = len(mat_slots)
            mat_slots.append(mat)
        mi = mat_slot_index[id(mat)]

        # Winding swapped relative to raw MDL2 order (confirmed to give
        # correct outward-facing shading for this source data).
        for a, b, c in tris:
            try:
                # [Fluffy Cupcake, 18.09.2026] CHANGED: triangle winding changed (was (a, c, b)); the comment above may be outdated.
                face = bm.faces.new((bm_verts[a], bm_verts[b], bm_verts[c]))
            except ValueError:
                continue  # duplicate face, skip
            # [Fluffy Cupcake, 18.09.2026] CHANGED: matches the changed winding above (was (a, c, b)).
            ordered = (a, b, c)
            for idx, loop in zip(ordered, face.loops):
                if idx < len(uvs_src) and uvs_src[idx]:
                    u, v = uvs_src[idx]
                    loop[uv_layer].uv = (u * uv_scale[0], v * uv_scale[1])
                if color_layer is not None and weights_src is not None and idx < len(weights_src):
                    w0, w1, w2, w3 = weights_src[idx]
                    loop[color_layer] = (w0, w1, w2, w3)
            face_material_pairs.append((face, mi))

    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    for face, mi in face_material_pairs:
        face.material_index = mi

    bm.to_mesh(mesh)
    bm.free()

    for mat in mat_slots:
        mesh.materials.append(mat)

    if len(all_normals) == len(mesh.vertices):
        try:
            mesh.use_auto_smooth = True
        except AttributeError:
            pass  # removed in newer Blender; custom split normals still apply
        try:
            mesh.normals_split_custom_set_from_vertices(all_normals)
        except Exception as e:  # noqa: BLE001
            print(f"[LR2 Import] Could not set custom normals for {name}: {e}")

    obj = bpy.data.objects.new(name, mesh)
    return obj


# ==========================================================================
# Import operator
# ==========================================================================


# >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: auto-detection of the Game Data root from the selected WRL path.
def infer_gamedata_root_from_wrl(wrl_path):
    """Infer the game root from .../<anything>/GAME DATA/SAVED WORLDS/<world>.WRL."""
    if not wrl_path:
        return ""

    path = Path(wrl_path)
    parts = list(path.parts)

    game_data_index = next(
        (i for i, part in enumerate(parts) if part.casefold() == "game data"),
        None,
    )
    if game_data_index is None or game_data_index == 0:
        return ""

    root = Path(*parts[:game_data_index])
    try:
        return str(root.resolve())
    except OSError:
        return str(root)


# <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: infer_gamedata_root_from_wrl.
class IMPORT_OT_lr2_world(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.lr2_world"
    bl_label = "Import LR2 World (.wrl)"
    bl_description = "Import a LEGO Racers 2 .wrl world with models, terrain and textures"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".wrl"
    filter_glob: StringProperty(default="*.wrl", options={"HIDDEN"})

    gamedata_dir: StringProperty(
        name="GAMEDATA Folder",
        description="Root folder produced by extracting GAMEDATA.GTC with UNGTC "
                    "(contains e.g. 'GAME DATA/SANDY ISLAND/...')",
        subtype="DIR_PATH",
    )
    convert_axes: BoolProperty(
        name="Convert Axes (Y-up -> Z-up)",
        description="Convert LR2's Y-up coordinate system to Blender's Z-up",
        default=True,
    )
    # >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: new import options: Sea Texture Tiles, WaterCourse Width/Longitudinal UV Factor.
    sea_texture_tiles: FloatProperty(
        name="Sea Texture Tiles",
        description="Number of times the Sea texture repeats across the generated Sea plane",
        default=20.0,
        min=1.0,
        max=200.0,
    )
    watercourse_flow_uv_factor: FloatProperty(
        name="WaterCourse Width UV Factor",
        description="World-space texture repeat density across WaterCourse width. 0.02 currently matches DINO and Mars.",
        default=0.02,
        min=0.0001,
        max=10.0,
    )
    watercourse_longitudinal_uv_factor: FloatProperty(
        name="WaterCourse Longitudinal UV Factor",
        description="Independent texture repeat density along WaterCourse length. DINO currently matches 0.08; Mars needs a much larger value.",
        default=0.08,
        min=0.0001,
        max=10.0,
    )
    # <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: new import options.
    import_terrain: BoolProperty(
        name="Import Terrain",
        description="Also import the level's terrain heightmap",
        default=True,
    )
    skip_the_pits: BoolProperty(
        name="Skip 'The Pits' Objects",
        description="Don't import cThePits entities",
        default=True,
    )
    # >>> [Kuchenkruste, 12.11.2026] BEGIN of added/changed code: "Skip Layer Sets" options (replace skip_races / group_zone_pairs / group_by_layer_sets).
    # Layer-set grouping is always active. A skipped set is not imported; an object that also belongs to a
    # non-skipped set (e.g. shared scenery) stays and is linked only into the non-skipped sets.
    skip_all: BoolProperty(
        name="All",
        description="Skip every layer set including 'Static'. Only the terrain (see 'Import Terrain') is still imported",
        default=False,
    )
    skip_static: BoolProperty(
        name="Static",
        description="Skip the 'Static' layer set (always-loaded world objects). The terrain is not "
                    "affected; it is controlled by 'Import Terrain'",
        default=False,
    )
    skip_races: BoolProperty(
        name="Races",
        description="Skip all 'Track N' layer sets (checkpoints, start positions, AI, track scenery)",
        default=False,
    )
    skip_bonus: BoolProperty(name="Bonus", description="Skip the 'Bonus' layer set", default=False)
    skip_battle: BoolProperty(name="Battle", description="Skip the 'Battle' layer set", default=False)
    skip_foyer: BoolProperty(name="Foyer", description="Skip the 'Foyer' layer set", default=False)
    skip_explore: BoolProperty(name="Explore", description="Skip the 'Explore' layer set", default=False)
    show_misc: BoolProperty(name="Misc", description="Show miscellaneous import options", default=False)
    import_markers: BoolProperty(
        name="Import Special Markers",
        description="Import model-less entities (race/foyer start positions, cinematic cameras, "
                    "reverb gateways) as Empties",
        default=False,
    )
    # <<< [Kuchenkruste, 12.11.2026] END of added/changed code.

    exp_unified_terrain_material: BoolProperty(
        name="Unified Terrain Material",
        description="Use ONE material for the whole terrain instead of one per texture combination "
                    "(Sandy Bay: ~75 materials). Blend weights are stored as per-vertex float "
                    "attributes 'TerrainW_<n>' (n = TEXTURE<n>.MIP); each texture is a single, "
                    "swappable Image Texture node named TEXTURE<n>",
        default=True,  # [Kuchenkruste, 12.11.2026] CHANGED: default was False
    )
    exp_terrain_collision_copy: BoolProperty(
        name="Terrain Collision Copy",
        description="Additionally create a geometry-only copy of the terrain ('<terrain>_collision') "
                    "whose triangles are assigned to materials COL_TEXTURE<n> by their dominant "
                    "texture. Intended for assigning collision types per texture. "
                    "Hidden in the viewport and render by default",
        default=False,
    )

    def execute(self, context):
        wrl_path = self.filepath
        # [Fluffy Cupcake, 18.09.2026] CHANGED: import log file next to the WRL (<name>_lr2_import.log) (next 3 lines + later log() calls).
        set_import_log(str(Path(wrl_path).with_name(Path(wrl_path).stem + "_lr2_import.log")))
        log(f"[LR2 Import] ===== START {wrl_path} =====")
        gamedata_root = self.gamedata_dir
        log(f"[LR2 Import] Initial GAMEDATA root={gamedata_root!r}")

        # [Fluffy Cupcake, 18.09.2026] CHANGED: Game Data path is auto-detected when the field is empty (next lines).
        # Automatically derive the game root from the selected WRL when the
        # field is blank. The outer "LEGO Racers 2" folder can have any name.
        if not gamedata_root:
            gamedata_root = infer_gamedata_root_from_wrl(wrl_path)
            if gamedata_root:
                self.gamedata_dir = gamedata_root
                log(f"[LR2 Import] Inferred GAMEDATA root={gamedata_root!r}")

        if not gamedata_root:
            # [Fluffy Cupcake, 18.09.2026] CHANGED: error message changed (previously: 'Please set the GAMEDATA folder in the import options panel.').
            self.report(
                {"ERROR"},
                "Could not infer the game root from this WRL. "
                "Expected .../GAME DATA/SAVED WORLDS/<world>.WRL; "
                "set GAMEDATA Folder manually.",
            )
            return {"CANCELLED"}

        try:
            entities = parse_wrl(wrl_path)
            # [Fluffy Cupcake, 18.09.2026] CHANGED: additional diagnostics for cPatch / cWaterCourse entities (next 5 lines).
            log(f"[LR2 Import] Parsed WRL entities={len(entities)}")
            patch_entities = [e for e in entities if e.type_id == "cPatch"]
            log(f"[LR2 Import] cPatch entities={len(patch_entities)}")
            watercourse_entities = [e for e in entities if e.type_id == "cWaterCourse"]
            log(f"[LR2 Import] cWaterCourse entities={len(watercourse_entities)}")
        except Exception as e:  # noqa: BLE001
            # [Fluffy Cupcake, 18.09.2026] CHANGED: error is now also written to the import log.
            log(f"[LR2 Import] WRL PARSE ERROR: {e}\n{traceback.format_exc()}")
            self.report({"ERROR"}, f"Failed to parse WRL: {e}")
            return {"CANCELLED"}

        collection_name = f"LR2_{Path(wrl_path).stem}"
        existing = bpy.data.collections.get(collection_name)
        if existing:
            # [Kuchenkruste, 11.11.2026] CHANGED: also remove nested collections/objects of a previous import.
            for child in list(existing.children_recursive):
                for obj in list(child.objects):
                    bpy.data.objects.remove(obj, do_unlink=True)
                bpy.data.collections.remove(child)
            for obj in list(existing.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.collections.remove(existing)
        collection = bpy.data.collections.new(collection_name)
        context.scene.collection.children.link(collection)

        material_cache = {}
        image_cache = {}  # resolved absolute texture path -> bpy.Image (shared, avoids re-decoding)
        model_cache = {}  # model_ref -> template bpy.types.Object (unlinked)
        collision_cache = {}  # [Kuchenkruste, 11.11.2026] CHANGED: model_ref -> terrain collision template (experimental option)
        layer_collections = {}  # layer int -> bpy.types.Collection (sub-collection of `collection`)
        zone_collections = {}  # zone id (layer with bit 0x200000 stripped) -> bpy.types.Collection
        name_to_obj = {}  # entity name -> placed instance (for binding/parenting resolution)
        placed = 0
        skipped = 0
        # [Fluffy Cupcake, 18.09.2026] CHANGED: collections for Sea / SkyBox / terrain objects (used for Sea sizing and height).
        sea_objects = []
        skybox_objects = []
        terrain_objects = []

        ZONE_FLAG = 0x200000

        # >>> [Kuchenkruste, 11.11.2026] BEGIN of added/changed code: layer-set hierarchy (set -> class collections).
        layer_sets = []
        for _e in entities:
            if _e.type_id == "cLayerManager":
                layer_sets = _e.props.get("layer_sets", [])
                break
        # Sets whose mask covers (almost) every index are unused placeholders (Sandy "Track 5/6"):
        # skip them, otherwise every object would be linked into them as well.
        active_sets = [(n, b) for n, b in layer_sets if 0 < len(b) < 24]
        use_sets = bool(active_sets)  # layer-set grouping is always on when the WRL has a layer manager
        # >>> [Kuchenkruste, 12.11.2026] BEGIN of added/changed code: layer-set skipping + static dedupe.
        def skipped_set(name):
            if self.skip_all:
                return True
            low = name.lower()
            if low.startswith("track"):
                return self.skip_races
            return {
                "static": self.skip_static,
                "bonus": self.skip_bonus, "battle": self.skip_battle,
                "foyer": self.skip_foyer, "explore": self.skip_explore,
            }.get(low, False)

        def target_sets(e):
            """Layer sets an entity is placed in (after skipping). Objects of the always-loaded
            'Static' set are placed only there. Empty list = entity is skipped."""
            idx = layer_index(e.layer)
            names = [n for n, b in active_sets if idx in b]
            if "Static" in names:
                names = ["Static"]
            if not names:
                names = ["Unassigned"]
            if e.type_id == "cLegoTerrain":  # terrain is only controlled by "Import Terrain"
                return names
            return [n for n in names if not skipped_set(n)]
        # <<< [Kuchenkruste, 12.11.2026] END of added/changed code.
        set_collections = {}
        class_collections = {}
        stem = Path(wrl_path).stem

        def _set_collection(set_name):
            c = set_collections.get(set_name)
            if c is None:
                c = bpy.data.collections.new(f"{stem} | {set_name}")
                collection.children.link(c)
                set_collections[set_name] = c
            return c

        if use_sets:
            for _n, _b in active_sets:  # create in layer-manager order
                if not skipped_set(_n) or (_n == "Static" and self.import_terrain):
                    _set_collection(_n)

        def _class_collection(set_name, class_name):
            key = (set_name, class_name)
            c = class_collections.get(key)
            if c is None:
                c = bpy.data.collections.new(f"{set_name} / {class_name}")
                _set_collection(set_name).children.link(c)
                class_collections[key] = c
            return c

        def link_entity(e, inst):
            """Link inst into the collection(s) of every layer set containing the entity's layer index."""
            idx = layer_index(e.layer)
            inst["lr2_class"] = e.type_id
            inst["lr2_layer"] = int(e.layer)
            inst["lr2_layer_index"] = idx
            if "checkpoint_index" in e.props:
                inst["lr2_checkpoint_index"] = int(e.props["checkpoint_index"])
            if not use_sets:
                get_layer_collection(e.layer).objects.link(inst)
                return
            names = target_sets(e)
            inst["lr2_layer_sets"] = ", ".join(names)
            for n in names:
                _class_collection(n, e.type_id).objects.link(inst)
        # <<< [Kuchenkruste, 11.11.2026] END of added/changed code.

        def get_layer_collection(layer_value):
            coll = layer_collections.get(layer_value)
            if coll is not None:
                return coll

            parent_coll = collection  # [Kuchenkruste, 12.11.2026] zone grouping removed (superseded by layer sets)

            coll = bpy.data.collections.new(f"Layer_{layer_value}")
            parent_coll.children.link(coll)
            layer_collections[layer_value] = coll
            return coll


        for e in entities:
            # [Kuchenkruste, 12.11.2026] CHANGED: Skip Layer Sets (replaces the former layer-detection based "Skip Races").
            if use_sets and e.type_id != "cLayerManager" and not target_sets(e):
                skipped += 1
                continue

            # >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: cSea import.
            # cSea is the global ocean surrounding the islands. It has no
            # finite geometry dimensions in the WRL, so generate a large plane.
            if e.type_id == "cSea":
                sea_name = e.name or "Sea"
                sea_height = float(e.props.get("sea_height", 0.0) or 0.0)
                sea_texture = e.props.get("sea_texture", "")
                rgb_s = e.props.get("sea_rgb_s", b"\\xff\\xff\\xff\\x00")

                inst = build_sea(
                    sea_name,
                    SEA_DEFAULT_PLANE_SIZE,
                    sea_height,
                    self.convert_axes,
                    material_cache,
                    image_cache,
                    sea_texture,
                    rgb_s,
                    gamedata_root,
                    self.sea_texture_tiles,
                )
                link_entity(e, inst)  # [Kuchenkruste, 11.11.2026] CHANGED: was get_layer_collection(e.layer).objects.link(inst)
                inst["lr2_sea_height"] = sea_height
                inst["lr2_sea_rise_lower"] = float(e.props.get("sea_rise_lower", 0.0) or 0.0)
                inst["lr2_sea_fade_distance"] = float(e.props.get("sea_fade_distance", 0.0) or 0.0)
                inst["lr2_sea_rgba"] = tuple(rgb_s) if rgb_s else (255, 255, 255, 0)
                inst["lr2_sea_texture"] = sea_texture
                inst["lr2_sea_texture_tiles"] = float(self.sea_texture_tiles)
                inst["lr2_sea_binding"] = (e.props.get("binding") or "").strip()
                sea_objects.append(inst)

                print(
                    f"[LR2 Import] Sea: height={sea_height:.4f}, "
                    f"rise_lower={float(e.props.get('sea_rise_lower', 0.0) or 0.0):.4f}, "
                    f"fade={float(e.props.get('sea_fade_distance', 0.0) or 0.0):.4f}, "
                    f"texture={sea_texture!r}, RGBS={tuple(rgb_s) if rgb_s else None}"
                )

                placed += 1
                name_to_obj[(e.name or "").strip()] = (
                    inst, (e.props.get("binding") or "").strip()
                )
                continue

            # <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: cSea import.
            # >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: cWaterSheet (water plane) import.
            # WaterSheets are procedural geometry embedded in the WRL.
            # They do not have a model reference, so handle them separately
            # from the normal MD2/terrain model path.
            if e.type_id == "cWaterSheet":
                water_texture = e.props.get("water_texture", "")
                reflection_texture = e.props.get("reflection_texture", "")
                width = float(e.props.get("width", 0.0) or 0.0)
                height = float(e.props.get("height", 0.0) or 0.0)

                if width <= 0.0 or height <= 0.0:
                    print(f"[LR2 Import] Invalid WaterSheet size for {e.name!r}: {width} x {height}")
                    skipped += 1
                    continue

                water_name = e.name or "WaterSheet"
                inst = build_water_sheet(
                    water_name, width, height, self.convert_axes,
                    material_cache, image_cache, water_texture,
                    reflection_texture, gamedata_root
                )
                link_entity(e, inst)  # [Kuchenkruste, 11.11.2026] CHANGED: was get_layer_collection(e.layer).objects.link(inst)

                pos = e.props.get("position", (0.0, 0.0, 0.0))
                rot = e.props.get("rotation", (0.0, 0.0, 0.0, 1.0))
                if self.convert_axes:
                    pos = convert_position(pos)
                    rot = convert_rotation(rot)

                inst.location = pos
                inst.rotation_mode = "QUATERNION"
                x, y, z, w = rot
                inst.rotation_quaternion = (w, x, y, z)

                placed += 1
                name_to_obj[(e.name or "").strip()] = (inst, "")
                continue

            # <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: cWaterSheet import.
            # >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: cWaterCourse import.
            if e.type_id == "cWaterCourse":
                points = e.props.get("points", [])
                if len(points) < 2:
                    log(f"[LR2 Import] SKIP cWaterCourse name={e.name!r}: only {len(points)} points")
                    skipped += 1
                    continue

                try:
                    inst = build_watercourse(
                        e.name or "WaterCourse",
                        points,
                        e.props.get("course_ref", (0.0, 0.0, 0.0)),
                        e.props.get("grid_resolution", 1),
                        e.props.get("bottom_layer_speed", 0.0),
                        e.props.get("top_layer_speed", 0.0),
                        e.props.get("wavyness", 0.0),
                        e.props.get("texture_disturbance", 0.0),
                        e.props.get("flow_direction", 0),
                        e.props.get("texture_tiling", 1.0),
                        e.props.get("texture_tiling_aux", 0.0),
                        self.watercourse_flow_uv_factor,
                        self.watercourse_longitudinal_uv_factor,
                        self.convert_axes,
                        material_cache,
                        image_cache,
                        e.props.get("water_texture", ""),
                        e.props.get("bed_texture", ""),
                        gamedata_root,
                    )
                    link_entity(e, inst)  # [Kuchenkruste, 11.11.2026] CHANGED: was get_layer_collection(e.layer).objects.link(inst)
                    inst["lr2_watercourse_course_ref"] = tuple(e.props.get("course_ref", (0.0, 0.0, 0.0)))
                    inst["lr2_watercourse_binding"] = (e.props.get("binding") or "").strip()
                    log(f"[LR2 Import] PLACED cWaterCourse name={inst.name!r} layer={e.layer} "
                        f"points={len(points)} grid={inst.get('lr2_watercourse_grid_size')} "
                        f"grid_res={int(e.props.get('grid_resolution', 1) or 1)} "
                        f"flow_dir={int(e.props.get('flow_direction', 0) or 0)} "
                        f"tex_tiling={float(e.props.get('texture_tiling', 1.0) or 1.0):.6g} "
                        f"uv_width={self.watercourse_flow_uv_factor:.6g} "
                        f"uv_long={self.watercourse_longitudinal_uv_factor:.6g}")
                    placed += 1
                    name_to_obj[(e.name or "").strip()] = (inst, (e.props.get("binding") or "").strip())
                except Exception as ex:  # noqa: BLE001
                    log(f"[LR2 Import] FAILED cWaterCourse name={e.name!r}: {ex}\n{traceback.format_exc()}")
                    skipped += 1
                continue

            # <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: cWaterCourse import.
            # >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: cPatch (land patch) model reference handling, skipped-counter logic, is_patch flag; the old skip_plasball check was removed here.
            # >>> [Kuchenkruste, 11.11.2026] BEGIN of added/changed code: marker entities (no model) -> Empties.
            _mspec = WRL_FORMATS.get((e.type_id, e.u))
            if _mspec is not None and _mspec.get("marker"):
                if not self.import_markers:
                    continue
                inst = bpy.data.objects.new(e.name or e.type_id, None)
                inst.empty_display_type = "ARROWS"
                inst.empty_display_size = 2.0
                link_entity(e, inst)  # [Kuchenkruste, 11.11.2026] CHANGED: was get_layer_collection(e.layer).objects.link(inst)
                pos = e.props.get("position", (0.0, 0.0, 0.0))
                rot = e.props.get("rotation", (0.0, 0.0, 0.0, 1.0))
                if self.convert_axes:
                    pos = convert_position(pos)
                    rot = convert_rotation(rot)
                inst.location = pos
                inst.rotation_mode = "QUATERNION"
                x, y, z, w = rot
                inst.rotation_quaternion = (w, x, y, z)
                inst["lr2_binding"] = (e.props.get("binding") or "").strip()
                placed += 1
                continue
            # <<< [Kuchenkruste, 11.11.2026] END of added/changed code.
            spec = WRL_FORMATS.get((e.type_id, e.u))
            if e.type_id == "cPatch":
                model_ref = e.props.get("model")
            else:
                if spec is None or spec["model"] is None:
                    continue
                model_ref = e.props.get(spec["model"])
            if not model_ref:
                if e.type_id == "cPatch":
                    log(f"[LR2 Import] SKIP cPatch name={e.name!r}: no model reference")
                skipped += 1
                continue

            if e.type_id == "cPatch":
                log(f"[LR2 Import] PATCH entity: name={e.name!r}, layer={e.layer}, "
                    f"u={e.u}, model_ref={model_ref!r}")

            is_terrain = e.type_id == "cLegoTerrain"
            is_patch = e.type_id == "cPatch"
            if is_terrain and not self.import_terrain:
                continue
            if self.skip_the_pits and e.type_id == "cThePits":
                skipped += 1
                continue
            # <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: cPatch handling.
            if model_ref not in model_cache:
                surfaces = None
                obj_name = None
                uv_scale = (1.0, 1.0)
                if is_terrain:
                    terrain_dir = resolve_path_ci(gamedata_root, model_ref)
                    if terrain_dir is not None:
                        terr_file = resolve_path_ci(str(terrain_dir), "TERRDATA.TDF")
                        if terr_file is not None:
                            try:
                                surfaces = parse_tdf(str(terr_file), terrain_dir=str(terrain_dir))
                                obj_name = f"terrain_{Path(model_ref).name}"
                                uv_scale = e.props.get("texture_scale", (1.0, 1.0)) or (1.0, 1.0)
                            except Exception as ex:  # noqa: BLE001
                                # [Fluffy Cupcake, 18.09.2026] CHANGED: print() -> log() (also written to the import log).
                                log(f"[LR2 Import] Failed to parse terrain {terr_file}: {ex}")
                    if surfaces is None:
                        # [Fluffy Cupcake, 18.09.2026] CHANGED: print() -> log().
                        log(f"[LR2 Import] Terrain not found/parsable: {model_ref}")
                else:
                    path = model_ref if model_ref.lower().endswith(".md2") else model_ref + ".md2"
                    resolved = resolve_path_ci(gamedata_root, path)
                    # [Fluffy Cupcake, 18.09.2026] CHANGED: additional cPatch diagnostics.
                    if is_patch:
                        log(f"[LR2 Import] Resolve cPatch model: root={gamedata_root!r} ref={model_ref!r} "
                            f"candidate={path!r} -> {str(resolved) if resolved else None!r}")
                    if resolved is not None:
                        try:
                            surfaces = parse_mdl2(str(resolved))
                            # [Fluffy Cupcake, 18.09.2026] CHANGED: additional cPatch diagnostics.
                            if is_patch:
                                log(f"[LR2 Import] Parsed cPatch model {resolved.name}: surfaces={len(surfaces)} "
                                    f"verts={sum(len(s.get('vertices', [])) for s in surfaces)}")
                            obj_name = Path(model_ref).stem
                        except Exception as ex:  # noqa: BLE001
                            # [Fluffy Cupcake, 18.09.2026] CHANGED: model parse errors and 'model not found' are now only logged for cPatch models (previously logged via print() for all models) - see also the next note.
                            if is_patch:
                                log(f"[LR2 Import] Failed to parse cPatch model {resolved}: {ex}\n{traceback.format_exc()}")
                    else:
                        # [Fluffy Cupcake, 18.09.2026] CHANGED: see note above: 'Model not found' message is now only logged for cPatch.
                        if is_patch:
                            log(f"[LR2 Import] cPatch model not found: {model_ref} | root={gamedata_root!r} | candidate={path!r}")

                if surfaces is not None:
                    # [Fluffy Cupcake, 18.09.2026] CHANGED: additional cPatch diagnostics.
                    if is_patch:
                        log(f"[LR2 Import] Building cPatch template for {obj_name!r} from {model_ref!r}")
                    template = build_object(
                        obj_name, surfaces, self.convert_axes, material_cache, image_cache, gamedata_root,
                        is_terrain=is_terrain, uv_scale=uv_scale,
                        # [Kuchenkruste, 11.11.2026] CHANGED: terrain_unified (experimental).
                        terrain_unified=is_terrain and self.exp_unified_terrain_material,
                    )
                    # >>> [Kuchenkruste, 11.11.2026] BEGIN of added/changed code: experimental terrain collision copy template.
                    if is_terrain and self.exp_terrain_collision_copy:
                        try:
                            collision_cache[model_ref] = build_terrain_collision_object(
                                f"{obj_name}_collision", surfaces, self.convert_axes, material_cache,
                            )
                        except Exception as ex:  # noqa: BLE001
                            log(f"[LR2 Import] Terrain collision copy FAILED: {ex}\n{traceback.format_exc()}")
                    # <<< [Kuchenkruste, 11.11.2026] END of added/changed code.
                else:
                    template = None
                model_cache[model_ref] = template

            template = model_cache[model_ref]
            if template is None:
                # [Fluffy Cupcake, 18.09.2026] CHANGED: additional cPatch diagnostics.
                if is_patch:
                    log(f"[LR2 Import] TEMPLATE FAILED for cPatch name={e.name!r} model={model_ref!r}")
                skipped += 1
                continue

            inst = template.copy()
            inst.data = template.data
            inst.name = e.name or e.type_id
            link_entity(e, inst)  # [Kuchenkruste, 11.11.2026] CHANGED: was get_layer_collection(e.layer).objects.link(inst)
            # Keep the import log useful during reverse engineering without
            # dumping thousands of ordinary GeneralStatic placements.
            # [Fluffy Cupcake, 18.09.2026] CHANGED: additional cPatch diagnostics.
            if is_patch:
                log(f"[LR2 Import] PLACED cPatch name={inst.name!r} model={model_ref!r} "
                    f"layer={e.layer}")

            pos = e.props.get("position", (0.0, 0.0, 0.0))
            rot = e.props.get("rotation", (0.0, 0.0, 0.0, 1.0))
            scale = e.props.get("scale", (1.0, 1.0, 1.0))
            if self.convert_axes:
                pos = convert_position(pos)
                rot = convert_rotation(rot)
                scale = (scale[0], scale[2], scale[1])

            inst.location = pos
            inst.rotation_mode = "QUATERNION"
            x, y, z, w = rot
            inst.rotation_quaternion = (w, x, y, z)
            inst.scale = scale
            # >>> [Kuchenkruste, 11.11.2026] BEGIN of added/changed code: place the experimental terrain collision copy at the terrain's transform.
            col_template = collision_cache.get(model_ref) if is_terrain else None
            if col_template is not None:
                col_inst = col_template.copy()
                col_inst.data = col_template.data
                col_inst.name = f"{inst.name}_collision"
                for coll in inst.users_collection:
                    coll.objects.link(col_inst)
                col_inst.location = inst.location
                col_inst.rotation_mode = "QUATERNION"
                col_inst.rotation_quaternion = inst.rotation_quaternion
                col_inst.scale = inst.scale
                col_inst.hide_render = True
                try:
                    col_inst.hide_set(True)
                except RuntimeError:
                    col_inst.hide_viewport = True
                log(f"[LR2 Import] Terrain collision copy placed: {col_inst.name!r}")
            # <<< [Kuchenkruste, 11.11.2026] END of added/changed code.
            # [Fluffy Cupcake, 18.09.2026] CHANGED: cPatch diagnostics and custom properties (lr2_object_type, lr2_patch_model, lr2_patch_binding).
            if is_patch:
                log(f"[LR2 Import] PATCH transform name={inst.name!r} location={tuple(inst.location)} "
                    f"rotation_quat={tuple(inst.rotation_quaternion)} scale={tuple(inst.scale)}")
                inst["lr2_object_type"] = "cPatch"
                inst["lr2_patch_model"] = model_ref
                inst["lr2_patch_binding"] = (e.props.get("binding") or "").strip()
            placed += 1

            # [Fluffy Cupcake, 18.09.2026] CHANGED: SkyBox / terrain objects are collected (needed for Sea sizing and Sea height).
            if e.type_id == "cSkyBox":
                skybox_objects.append(inst)
            elif e.type_id in {"cLegoTerrain", "cTerrain2"}:
                terrain_objects.append(inst)

            binding = (e.props.get("binding") or "").strip()
            name_to_obj[(e.name or "").strip()] = (inst, binding)

        # >>> [Fluffy Cupcake, 18.09.2026] BEGIN of added/changed code: Sea plane sizing (SkyBox-derived) and Sea height conversion.
        # cSea follows the player's view in-game, so it has no finite WRL
        # dimensions. Match the generated plane to the horizontal size of the
        # imported SkyBox when one exists.
        #
        # cSea Hght is translated from the terrain/world height reference.
        # Do not apply terrain mesh scale to Hght; Sandy's controlled WRL edit
        # establishes the Sea height as a direct height value.
        if sea_objects:
            sea_size = SEA_DEFAULT_PLANE_SIZE
            valid_skyboxes = [
                obj for obj in skybox_objects
                if obj.dimensions.x > 0.0 and obj.dimensions.y > 0.0
            ]
            if valid_skyboxes:
                sea_size = max(
                    max(float(obj.dimensions.x), float(obj.dimensions.y))
                    for obj in valid_skyboxes
                )

            # Keep the Sea object's Blender scale at exactly 1.0.
            # The previous implementation used object scale to match the
            # imported SkyBox (e.g. 0.2, 0.2, 1.0 on Sandy).  We now bake
            # that horizontal sizing into the mesh itself and make the Sea
            # 50% of the previous SkyBox-derived size.
            factor = sea_size / SEA_DEFAULT_PLANE_SIZE
            mesh_factor = factor * 0.5
            for sea_obj in sea_objects:
                for vert in sea_obj.data.vertices:
                    # build_sea() has already converted LR2 Y-up coordinates
                    # to Blender Z-up, so X/Y are the horizontal axes here.
                    vert.co.x *= mesh_factor
                    vert.co.y *= mesh_factor
                sea_obj.scale = (1.0, 1.0, 1.0)

                h = float(sea_obj.get("lr2_sea_height", 0.0) or 0.0)
                binding = (sea_obj.get("lr2_sea_binding") or "").strip()

                # SEA HEIGHT CONVERSION:
                # cSea Hght is expressed relative to the terrain's vertical
                # scale. Controlled Sandy test:
                #     Hght 127 / terrain Z scale 0.85 = 149.4118
                # which matches the observed in-game Sea height.
                #
                # Do NOT parent the Sea to the terrain here. The terrain
                # translation is not part of this conversion.
                terrain_obj = terrain_objects[0] if terrain_objects else None
                terrain_z_scale = (
                    abs(float(terrain_obj.scale.z))
                    if terrain_obj is not None and abs(float(terrain_obj.scale.z)) > 1e-8
                    else 1.0
                )
                sea_z = h / terrain_z_scale

                if self.convert_axes:
                    sea_obj.location = convert_position((0.0, sea_z, 0.0))
                else:
                    sea_obj.location = (0.0, sea_z, 0.0)

                print(
                    f"[LR2 Import] Sea {sea_obj.name!r}: "
                    f"Hght={h:.4f} / terrain_Z_scale={terrain_z_scale:.6f} "
                    f"-> Blender Z={sea_obj.location.z:.4f}"
                )

            print(f"[LR2 Import] Sea plane size: {sea_size:.3f}")

        # <<< [Fluffy Cupcake, 18.09.2026] END of added/changed code: Sea sizing / height.
        # Second pass: resolve "binding" cross-references into real parenting
        # (e.g. CPspinner -> its CPfloater). Done after everything is placed
        # so file order doesn't matter. World-space transform is preserved
        # via matrix_parent_inverse ("keep transform" style parenting).
        parented = 0
        for name, (inst, binding) in name_to_obj.items():
            if not binding:
                continue
            target = name_to_obj.get(binding)
            if target is None:
                continue
            parent_obj = target[0]
            if parent_obj is inst:
                continue
            inst.parent = parent_obj
            inst.matrix_parent_inverse = parent_obj.matrix_world.inverted()
            parented += 1

        # [Kuchenkruste, 11.11.2026] remove empty layer-set / class collections
        for _c in list(class_collections.values()) + list(set_collections.values()):
            if not _c.objects and not _c.children:
                bpy.data.collections.remove(_c)

        self.report(
            {"INFO"},
            f"LR2 import done: {placed} placed ({parented} parented via binding), {skipped} skipped.",
        )
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout

        # [Fluffy Cupcake, 18.09.2026] CHANGED: show the auto-detected Game Data root in the import panel (next 5 lines).
        # Show the inferred root immediately after a WRL has been selected.
        # A manually entered path is never overwritten.
        if not self.gamedata_dir and self.filepath:
            inferred = infer_gamedata_root_from_wrl(self.filepath)
            if inferred:
                self.gamedata_dir = inferred

        layout.prop(self, "gamedata_dir")

        # >>> [Kuchenkruste, 12.11.2026] BEGIN of added/changed code: sectioned import panel.
        box = layout.box()
        box.label(text="Skip Layer Sets")
        col = box.column(align=True)
        col.prop(self, "skip_all")
        sub = col.column(align=True)
        sub.enabled = not self.skip_all
        sub.prop(self, "skip_static")
        sub.prop(self, "skip_races")
        sub.prop(self, "skip_bonus")
        sub.prop(self, "skip_battle")
        sub.prop(self, "skip_foyer")
        sub.prop(self, "skip_explore")

        box = layout.box()
        box.label(text="Terrain")
        col = box.column(align=True)
        col.prop(self, "import_terrain")
        sub = col.column(align=True)
        sub.enabled = self.import_terrain
        sub.prop(self, "exp_unified_terrain_material")

        box = layout.box()
        box.prop(
            self, "show_misc",
            icon="TRIA_DOWN" if self.show_misc else "TRIA_RIGHT",
            emboss=False,
        )
        if self.show_misc:
            col = box.column(align=True)
            col.prop(self, "convert_axes")
            col.prop(self, "skip_the_pits")
            col.prop(self, "import_markers")
            sub = col.column(align=True)
            sub.enabled = self.import_terrain
            sub.prop(self, "exp_terrain_collision_copy")
        # <<< [Kuchenkruste, 12.11.2026] END of added/changed code.


def menu_func_import(self, context):
    self.layout.operator(IMPORT_OT_lr2_world.bl_idname, text="LEGO Racers 2 World (.wrl)")


def register():
    bpy.utils.register_class(IMPORT_OT_lr2_world)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(IMPORT_OT_lr2_world)


if __name__ == "__main__":
    register()
