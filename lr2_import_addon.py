# ==========================================================================
# LEGO Racers 2 World Importer for Blender
#
# Copyright (C) 2026  [Your Name Here]
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
# ==========================================================================

bl_info = {
    "name": "LEGO Racers 2 World Importer",
    "author": "lr2-tools",
    "version": (1, 0, 0),
    "blender": (3, 2, 0),
    "location": "File > Import > LEGO Racers 2 World (.wrl)",
    "description": "Imports LR2 .wrl worlds (models + terrain + textures) directly, "
                   "reverse-engineered from the Whirled project and LibLR2.dll.",
    "category": "Import-Export",
}

import bpy
import bmesh
import math
import os
import struct
from pathlib import Path

from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper


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

AXIS_MATRIX = ((-1, 0, 0), (0, 0, 1), (0, 1, 0))


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
    raise ValueError(f"Unknown property kind: {kind}")


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
            if spec is not None:
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
# MDL2 (.md2) model parsing
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


def parse_mdl2(path):
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

        texture_label = f"blend_{'_'.join(str(t) for t in tex_key)}"
        if terrain_tex_paths[0]:
            texture_label = terrain_tex_paths[0]

        surfaces.append(
            {
                "vertices": verts, "normals": surf["normals"], "uvs": surf["uvs"], "indices": indices,
                "fill_type": 0, "texture": texture_label, "material": None,
                "mix_weights": surf["mix_weights"], "terrain_tex_paths": terrain_tex_paths,
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


def build_object(name, surfaces, convert_axes, material_cache, image_cache, gamedata_root,
                  is_terrain=False, uv_scale=(1.0, 1.0)):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new()
    color_layer = bm.loops.layers.color.new(TERRAIN_MIX_LAYER_NAME) if is_terrain else None

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

        if is_terrain:
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
                face = bm.faces.new((bm_verts[a], bm_verts[c], bm_verts[b]))
            except ValueError:
                continue  # duplicate face, skip
            ordered = (a, c, b)
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
    skip_plasball: BoolProperty(
        name="Skip Plasball Pickups",
        description="Don't import 'plasball' weapon pickup objects",
        default=False,
    )
    group_zone_pairs: BoolProperty(
        name="Group Paired Layers into Zones",
        description="Layer values observed in pairs differing by bit 0x200000 "
                    "(e.g. 98 and 2097250) appear to represent gameplay-critical "
                    "vs. decorative/pickup objects of the same route zone. When "
                    "enabled, their Layer_<n> collections are nested under a "
                    "shared Zone_<n> collection instead of sitting side by side.",
        default=True,
    )

    def execute(self, context):
        wrl_path = self.filepath
        gamedata_root = self.gamedata_dir

        if not gamedata_root:
            self.report({"ERROR"}, "Please set the GAMEDATA folder in the import options panel.")
            return {"CANCELLED"}

        try:
            entities = parse_wrl(wrl_path)
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, f"Failed to parse WRL: {e}")
            return {"CANCELLED"}

        collection_name = f"LR2_{Path(wrl_path).stem}"
        existing = bpy.data.collections.get(collection_name)
        if existing:
            for obj in list(existing.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.collections.remove(existing)
        collection = bpy.data.collections.new(collection_name)
        context.scene.collection.children.link(collection)

        material_cache = {}
        image_cache = {}  # resolved absolute texture path -> bpy.Image (shared, avoids re-decoding)
        model_cache = {}  # model_ref -> template bpy.types.Object (unlinked)
        layer_collections = {}  # layer int -> bpy.types.Collection (sub-collection of `collection`)
        zone_collections = {}  # zone id (layer with bit 0x200000 stripped) -> bpy.types.Collection
        name_to_obj = {}  # entity name -> placed instance (for binding/parenting resolution)
        placed = 0
        skipped = 0

        ZONE_FLAG = 0x200000

        def get_layer_collection(layer_value):
            coll = layer_collections.get(layer_value)
            if coll is not None:
                return coll

            parent_coll = collection
            if self.group_zone_pairs:
                zone_id = layer_value & ~ZONE_FLAG
                zone_coll = zone_collections.get(zone_id)
                if zone_coll is None:
                    zone_coll = bpy.data.collections.new(f"Zone_{zone_id}")
                    collection.children.link(zone_coll)
                    zone_collections[zone_id] = zone_coll
                parent_coll = zone_coll

            coll = bpy.data.collections.new(f"Layer_{layer_value}")
            parent_coll.children.link(coll)
            layer_collections[layer_value] = coll
            return coll

        for e in entities:
            spec = WRL_FORMATS.get((e.type_id, e.u))
            if spec is None or spec["model"] is None:
                continue
            model_ref = e.props.get(spec["model"])
            if not model_ref:
                continue

            is_terrain = e.type_id == "cLegoTerrain"
            if is_terrain and not self.import_terrain:
                continue
            if self.skip_the_pits and e.type_id == "cThePits":
                skipped += 1
                continue
            if self.skip_plasball and "plasball" in (e.name or "").lower():
                skipped += 1
                continue

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
                                print(f"[LR2 Import] Failed to parse terrain {terr_file}: {ex}")
                    if surfaces is None:
                        print(f"[LR2 Import] Terrain not found/parsable: {model_ref}")
                else:
                    path = model_ref if model_ref.lower().endswith(".md2") else model_ref + ".md2"
                    resolved = resolve_path_ci(gamedata_root, path)
                    if resolved is not None:
                        try:
                            surfaces = parse_mdl2(str(resolved))
                            obj_name = Path(model_ref).stem
                        except Exception as ex:  # noqa: BLE001
                            print(f"[LR2 Import] Failed to parse model {resolved}: {ex}")
                    else:
                        print(f"[LR2 Import] Model not found: {model_ref}")

                if surfaces is not None:
                    template = build_object(
                        obj_name, surfaces, self.convert_axes, material_cache, image_cache, gamedata_root,
                        is_terrain=is_terrain, uv_scale=uv_scale,
                    )
                else:
                    template = None
                model_cache[model_ref] = template

            template = model_cache[model_ref]
            if template is None:
                skipped += 1
                continue

            inst = template.copy()
            inst.data = template.data
            inst.name = e.name or e.type_id
            get_layer_collection(e.layer).objects.link(inst)

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
            placed += 1

            binding = (e.props.get("binding") or "").strip()
            name_to_obj[(e.name or "").strip()] = (inst, binding)

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

        self.report(
            {"INFO"},
            f"LR2 import done: {placed} placed ({parented} parented via binding), {skipped} skipped.",
        )
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "gamedata_dir")
        layout.prop(self, "convert_axes")
        layout.prop(self, "import_terrain")
        layout.prop(self, "skip_the_pits")
        layout.prop(self, "skip_plasball")
        layout.prop(self, "group_zone_pairs")


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
