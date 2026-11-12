# LEGO Racers 2 World Importer for Blender

A Blender addon that imports **LEGO Racers 2** (2001) `.wrl` world files
directly into Blender — models, terrain (with multi-texture blending), and
textures — reverse-engineered from the game's proprietary binary formats.

No intermediate `.obj`/`.mtl`/`.png` files are produced: meshes, materials
and images are built natively in Blender via `bmesh` and the shader node
system.

![Demo4](pics/Screen4.gif)

![Demo6](pics/Screen6.gif)

## Change Notes

v1.4.0

- Unified terrain material (single material for the whole terrain) and optional terrain collision copy
- Objects are sorted into the game's layer sets (Static, Track N, Bonus, Battle, Foyer, Explore) read from the WRL's layer manager, with the object class as second level; "Skip Layer Sets" options
- cCheckPoint support, optional special markers (start positions etc.)
- Import panel reorganised into sections; "Group Paired Layers into Zones" and the layer-detection based "Skip Races" were replaced

v1.3.0 - Changes by Fluffy Cupcake:

- Supports land patches (cPatch)
- Supports Sea (cSea)
- Supports WaterCourse (cWaterCourse)
- Supports Water Planes (cWaterSheet)
- Has the correct X axis (AXIS_MATRIX)
- Game Data path is auto-detected based on the selected WRL
- Replaced the "skip plasball" toggle with "skip races" (works via layer detection)
- MDL0 patch-model parser (parse_mdl0) and MDL0 dispatch in parse_mdl2
- IFL image-sequence resolution for Sea textures
- Import log file (<world>_lr2_import.log) and extended diagnostics
- New import options (Sea Texture Tiles, WaterCourse UV factors)
- Triangle winding order changed in build_object
- Model parse/"not found" messages are now only logged for cPatch models

## Main Features

- Parses `.wrl` world files (entity container format) and places every
  referenced object at its correct position, rotation and scale.
- Imports `.md2` (MDL2) models with vertex normals, UVs and material colors.
- Imports terrain (`TERRDATA.TDF`) as a single mesh with real multi-texture
  blending (up to 4 layers per vertex, matching the original terrain
  shader's weighting).
- Decodes the game's `.MIP` textures natively in Python (no external image
  library required) — `.MIP` files turned out to be the classic Truevision
  TGA format under a different extension.
- Alpha/transparency handling that follows the original engine's per-material
  `alpha_type` (cutout / soft blend / additive-approximated), instead of a
  one-size-fits-all transparency mode.
- Resolves the WRL's `binding` cross-references into real Blender parent/child
  relationships (e.g. checkpoint decorations parented to their checkpoint).

![Demo3](pics/Screen7.gif)



## Requirements

- A legally owned copy of LEGO Racers 2, with its `GAMEDATA.GTC` archive
  already extracted (e.g. via [UNGTC](https://github.com/JrMasterModelBuilder/UNGTC))
  into a folder on disk.
- v1.0.0 was developed and tested on **Blender 3.2**; later 3.x and 4.x
  versions should theoretically work but have not been verified.
- v1.3.0 was tested on **Blender 4.5.14 LTS**, aswell as other 4.X and 5.X versions
- v1.4.0 was tested on **Blender 5.2**

## Installation

1. Download `lr2_import_addon.py` from this repository.
2. In Blender: **Edit → Preferences → Add-ons → Install...**, select the file.
3. Enable the checkbox next to "LEGO Racers 2 World Importer".

## Usage

**File → Import → LEGO Racers 2 World (.wrl)**, then pick a `.wrl` file
(typically under `GAMEDATA\...\SAVED WORLDS\`).

![Demo01](pics/Screen01.png)


## Import Options

| Option                       | Description                                                                                                                                                                                                                   |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **GAMEDATA Folder**          | Root of your extracted game data. Detected automatically from the selected `.wrl` if left empty.                                                                                                                              |
| **Skip Layer Sets**          | Skips whole layer sets on import: **All** (everything, including Static; only the terrain is still imported), **Static**, **Races** (all `Track N` sets), **Bonus**, **Battle**, **Foyer** and **Explore**. The individual options are disabled while *All* is on. The terrain is never affected, see *Import Terrain*. |
| **Import Terrain**           | Also imports the level's terrain heightmap.                                                                                                                                                                                   |
| **Unified Terrain Material** | Uses one material for the whole terrain instead of one per texture combination (e.g. ~75 on Sandy Bay). On by default.                                                                                                       |
| **Convert Axes**             | *(Misc)* Converts LR2's Y-up coordinate system to Blender's Z-up. Leave on unless you have a specific reason not to.                                                                                                          |
| **Skip 'The Pits' Objects**  | *(Misc)* Excludes `cThePits` entities.                                                                                                                                                                                        |
| **Import Special Markers**   | *(Misc)* Imports model-less entities (race and foyer start positions, cinematic cameras, reverb gateways) as Empties. Off by default.                                                                                         |
| **Terrain Collision Copy**   | *(Misc)* Adds a hidden, geometry-only copy of the terrain (`<terrain>_collision`) whose triangles use materials `COL_TEXTURE<n>` by dominant texture, e.g. for assigning collision types. Off by default.                    |



![Demo02](pics/Screen02.jpg)


## How it works (short version)

- `.wrl` files are a binary entity container (not VRML, despite the
  extension) — a sequence of typed chunks, each with common fields
  (`layer`, `name`) plus type-specific fields (position, rotation, scale,
  a `binding` cross-reference, and for most types a `model` reference).
- `.md2` model files are a custom chunked format (`MDL1`/`MDL2` header +
  `GEO1` geometry chunk), unrelated to id Software's Quake II MD2 format
  despite sharing a name and extension.
- `TERRDATA.TDF` stores a 32×32 grid of terrain chunks, each with a
  16-bit height, an 8-bit packed normal, and a 16-bit `mix_ratios` field
  whose four nibbles are the blend weights for up to four textures.
- `.MIP` texture files are byte-for-byte the classic Truevision TGA format,
  just renamed. Supported subset: uncompressed types 1 (paletted) and 2
  (true-colour), 24 or 32 bit.
![Demo1](pics/Screen1.gif)

![Demo1](pics/Screen8.gif)


## Credits & sources

This addon builds on the work of several people who reverse-engineered
LEGO Racers 2's file formats before this project existed:

- **Fluffy Cupcake** – extended the importer for version 1.3.0
- **[GiantBlargg](https://github.com/GiantBlargg)** — author of
  [Whirled](https://github.com/GiantBlargg/Whirled), a Godot-based LR2
  viewer whose C++ source (`wrl.cpp`, `wrl_formats.cpp`, `mdl2.cpp`,
  `tdf.cpp`, among others) documents the WRL, MDL2 and TDF formats in
  detail. The corresponding parsing logic in this addon is a Python
  reimplementation based on that source, **used with GiantBlargg's
  explicit permission**. Whirled's repository does not carry a license
  file of its own; please contact the author before reusing its code
  independently of this project.
- **The [Rock Raiders United](https://rockraidersunited.com/) community**,
  in particular the contributors to the forum thread
  ["All About WRLs"](https://rockraidersunited.com/topic/5176-all-about-wrls-everything-you-see-and-do-in-the-worlds-warning-large-post/)
  (notably **dead_name** and **Fluffy Cupcake**), whose years of manual
  hex-diffing first documented the WRL entity format and provided the
  conceptual groundwork this project could build on.
- **dead_name**, author of the `LR2ModelViewer`/`LibLR2.dll` tool. This
  addon's `.MIP` texture decoder was derived independently by observing
  that tool's behaviour through clean-room .NET IL disassembly (a custom
  ECMA-335 metadata reader and IL disassembler, written from scratch for
  this purpose) — no code from the tool itself was copied.

## Important notes

- This project has no affiliation with the LEGO Group and is not endorsed
  by it.
- Game assets and extracted content are copyrighted material of the
  original game, LEGO Racers 2, and are therefore not shipped with this
  addon's repository.
- The addon was tested on Blender 3.2; later Blender 3.x and 4.x versions
  should theoretically work but have not been tested.
- Parts of this addon's source code were developed with the assistance of
  an AI coding tool, used under the author's direction and subject to the
  author's own design decisions, testing, and verification.

## License

Licensed under the **GNU General Public License v3.0 (GPLv3)**.

See [`LICENSE`](LICENSE) for the full text, or obtain it directly from the
Free Software Foundation:

```bash
curl -o LICENSE https://www.gnu.org/licenses/gpl-3.0.txt
```
