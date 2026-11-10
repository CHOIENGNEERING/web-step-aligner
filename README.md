<div align="center">

```
 ██╗    ██╗███████╗██████╗       ███████╗████████╗███████╗██████╗
 ██║    ██║██╔════╝██╔══██╗      ██╔════╝╚══██╔══╝██╔════╝██╔══██╗
 ██║ █╗ ██║█████╗  ██████╔╝█████╗███████╗   ██║   █████╗  ██████╔╝
 ██║███╗██║██╔══╝  ██╔══██╗╚════╝╚════██║   ██║   ██╔══╝  ██╔═══╝
 ╚███╔███╔╝███████╗██████╔╝      ███████║   ██║   ███████╗██║
  ╚══╝╚══╝ ╚══════╝╚═════╝       ╚══════╝   ╚═╝   ╚══════╝╚═╝
        █████╗ ██╗     ██╗ ██████╗ ███╗   ██╗███████╗██████╗
       ██╔══██╗██║     ██║██╔════╝ ████╗  ██║██╔════╝██╔══██╗
       ███████║██║     ██║██║  ███╗██╔██╗ ██║█████╗  ██████╔╝
       ██╔══██║██║     ██║██║   ██║██║╚██╗██║██╔══╝  ██╔══██╗
       ██║  ██║███████╗██║╚██████╔╝██║ ╚████║███████╗██║  ██║
       ╚═╝  ╚═╝╚══════╝╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝
```

# Web-STEP-Aligner 

**Drop a STEP file in the browser. Get it back perfectly posed.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Kernel](https://img.shields.io/badge/Geometry%20Kernel-OpenCASCADE%208.0%20(cascadio)-00E5FF)](https://github.com/trimesh/cascadio)
[![UI](https://img.shields.io/badge/UI-Plotly%20Dash-FF2D95)](https://dash.plotly.com/)
[![Port](https://img.shields.io/badge/Port-8052-9D00FF)](http://127.0.0.1:8052)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen)](#contributing)

> **A lightweight web-based microservice to easily align, rotate, and scale your STEP files — without heavy CAD software.**
>
> 无需沉重的 CAD 软件，在浏览器里轻松对齐 / 旋转 / 缩放 STEP 模型。

</div>
https://github.com/user-attachments/assets/41dc07a5-c1a4-42f0-a6b5-bf713999719f
---

## ⚡ Why this exists

Your vendor sends you a STEP file. It is beautiful. It is also:

- rotated 90° about an axis nobody documented,
- sitting 1,342.7 mm off the origin,
- in millimetres when your assembly is in metres,
- and the only tool you own that can fix it takes **four minutes to launch** and **2 GB of RAM to rotate a box**.

`Web-STEP-Aligner` is a **one-page** cure for that. It reads the BREP, shows you the part, lets you snap it onto a canonical pose (or a face, or a datum), and hands you back a clean `STEP` file — from a browser tab on `localhost:8052`.

No account. No cloud upload. No license server. Your geometry never leaves your machine.

---

## 🧩 Core features

| | Feature | What it actually means |
|---|---|---|
| 🌀 | **Zero install for the end user** | The whole tool is a local web page. `python app.py` → open `127.0.0.1:8052`. No CAD seat, no plugin, no license. |
| 📦 | **Real STEP, real BREP** | `STEPControl_Reader` / `STEPControl_Writer` (AP203 / AP214 / AP242). Faces, edges and assembly structure survive — nothing is tessellated to death and re-exported as a soup of triangles. |
| 🧠 | **OpenCASCADE geometry kernel** | `cascadio` ships the same OCC that sits inside FreeCAD and KiCad StepUp — as a plain `pip install` wheel, no conda. |
| 🎯 | **Principal-axis auto-align** | PCA over surface-area-weighted face centroids → minimal bounding box → snap longest axis to X, mid axis to Y, thinnest to Z. One click, deterministic, symmetric about the origin. |
| 🖱️ | **Face-to-face / plane snapping** | Pick two planar faces (or one face + world axis) and the part rotates so the normals agree, then translates to a chosen datum. |
| 🎚️ | **Interactive transform panel** | Live Euler rotations (XYZ / ZYX), translation vector, uniform or per-axis scale — every widget updates the preview before you commit. |
| 🔍 | **Measurement-grade readback** | Live bounding box, part volume, center of mass and face count shown next to the viewport, so you can *verify* the pose instead of trusting it. |
| 💾 | **One-click download** | `GET /api/export` streams a re-written STEP with the transform baked into the geometry (not a wrapper transform that downstream tools ignore). |
| 🧾 | **Transform receipt** | Every export ships a `.json` sidecar: the exact rotation matrix, translation, scale and the pre/post bounding box — auditable, reproducible, diff-able in git. |
| 🕶️ | **Cyberpunk UI** | Neon wireframe overlay, dark grid, glowing edges. Because a CAE tool is allowed to look like a nightclub. |

> **Scope note:** this project only *poses* geometry. It does not heal topology, stitch gaps or rebuild surfaces — alignment and rigid/similarity transforms, nothing else.

---
<img width="1882" height="892" alt="Image" src="https://github.com/user-attachments/assets/68c3e2e3-e587-4518-b645-61dce6539991" />
<!--
  ⬆️ TODO（留给你自己填）：把赛博朋克风格截图放到 docs/screenshot-cyberpunk.png
  建议构图：左 2/3 是黑底 + 青/品红发光边线的三维视口，右 1/3 是暗紫色玻璃拟态控制面板，
  顶部显示 "Web-STEP-Aligner v0.1" 与绿色 "OCC 7.7 · AP214" 状态灯。
  截图尺寸建议 1600×1000（Retina 2x 更佳），文件名保持 screenshot-cyberpunk.png。
-->

---

## 🏗 Architecture

```
┌─────────────────────────────┐
│  Browser (Dash · Plotly)    │   drag & drop, 3D preview, sliders
└──────────────┬──────────────┘
               │  JSON  (transform params)
┌──────────────▼──────────────┐
│  Flask / Dash callbacks     │   app.py  ·  session store (jobs/<sig>/)
└──────────────┬──────────────┘
               │  TopoDS_Shape  +  gp_Trsf
┌──────────────▼──────────────┐
│  OCC 8.0 (cascadio) + 文本改写 │   STEP read → tessellate → rewrite → STEP write
└──────────────┬──────────────┘
               │
     ┌─────────▼─────────┐
     │  <name>_aligned.step │  +  <name>_aligned.json (receipt)
     └───────────────────┘
```

The kernel is stateless and CPU-only. One process, one port, no database — the only state is a temp directory per session, cleaned on exit.

---
<img width="1882" height="892" alt="Screenshot 2026-11-10 044204" src="https://github.com/user-attachments/assets/f9b2d947-d288-48dd-8e61-4aad1f89386c" />


## 🚀 Quick start

### 0. Requirements

- Python **3.10+**（本机 3.15 已验证）
- Windows 10/11 / Linux / macOS
- **不需要 conda，不需要 pythonocc-core**：几何内核是 `cascadio`（wheel 里自带 OpenCASCADE 8.0，`pip install` 即装即用），STEP 的读 / 改写是 `aligner_core.py` 里自带的 ISO-10303-21 解析器。

### Install

```bash
cd Web-STEP-Aligner
python -m pip install -r requirements.txt
```

`requirements.txt`

```txt
dash>=4.0
plotly>=5.20
flask>=3.0
numpy>=1.24
cascadio>=0.1.0      # OpenCASCADE 三角化 + 平面解析数据（wheel 内置 OCC）
```

### Run it

```bash
python app.py
# 或者双击  start_8052.bat   （自动补依赖 + 开浏览器）
```

```
  ▸ Web-STEP-Aligner  v0.1.0
  ▸ kernel   cascadio / OpenCASCADE
  ▸ workdir  ./jobs
  ▸ ready    http://127.0.0.1:8052
```

Open **http://127.0.0.1:8052** → drop a `.step` / `.stp` → align → download.

```bash
# optional
export ALIGNER_PORT=8052        # Windows: set ALIGNER_PORT=8052
export ALIGNER_MAX_MB=200       # upload cap
export ALIGNER_DEBUG=1          # Dash 调试模式
```

### 两个引擎，一份几何

| 用途 | 实现 | 说明 |
|---|---|---|
| 预览 / 测量 / 拾取面 | `cascadio` → GLB（`include_brep=True`） | OCC 三角化，附带每个面的 plane / cylinder 解析参数，用于面法向列表与面贴合 |
| 导出 | 纯 Python 改写 STEP 文本 | 逐条重写 `CARTESIAN_POINT` / `DIRECTION` / `VECTOR` 模长 / 各类半径实体，变换**烘进 BREP**，不重新三角化、不丢拓扑 |

> 已经踩过的坑（改代码前必读）：STEP 里 `*_SHAPE_REPRESENTATION` 自带的 `AXIS2_PLACEMENT_3D` 会被读取器当成零件坐标系再乘一次，导出时**必须原样保留**，否则模型会被变换两遍。

### 自检

```bash
python tests/selftest.py      # 16 项：包围盒 / 体积 / 最小包围盒 / 导出回读 / 缩放 / 面拾取
```

---

## 🎛 Alignment modes

| Mode | Input | Use when |
|---|---|---|
| `principal` | nothing | Fastest "just make it straight". PCA + minimal bbox. |
| `face` | 2 face ids, or 1 face + target axis | A mating face must sit on XY. |
| `points` | 3 picked vertices | Classic 3-2-1 datum alignment. |
| `euler` | rx, ry, rz (deg), order | You already know the numbers. |
| `center` | bbox / mass center | Re-origin only, keep orientation. |

All modes compose with the scale field: `1.0` = keep, `25.4` = inch→mm, `0.001` = mm→m.

---

## 🔌 HTTP API

Everything the UI does is a plain endpoint — scriptable from CI, Blender, or your CAE pre-processor.

| Method | Endpoint | Body / Query | Returns |
|---|---|---|---|
| `POST` | `/api/upload` | `multipart/form-data: file` | `{ sid, bbox, volume, faces }` |
| `POST` | `/api/align` | `{ sid, mode, params }` | `{ bbox, volume, matrix }` |
| `POST` | `/api/transform` | `{ sid, translate, rotate, scale }` | `{ bbox, volume, matrix }` |
| `GET` | `/api/mesh` | `?sid&tol=` | tessellated preview (JSON) |
| `GET` | `/api/export` | `?sid&ap=214` | `application/step` download |
| `GET` | `/api/receipt` | `?sid` | transform receipt JSON |

```bash
# align a part and grab the result, headless
SID=$(curl -s -F file=@bracket.stp http://127.0.0.1:8052/api/upload | jq -r .sid)
curl -s -X POST http://127.0.0.1:8052/api/align \
     -H 'Content-Type: application/json' \
     -d "{\"sid\":\"$SID\",\"mode\":\"principal\"}" | jq
curl -s "http://127.0.0.1:8052/api/export?sid=$SID&ap=214" -o bracket_aligned.step
```

---

## ⚠️ Known limits (read before filing an issue)

- **Assemblies:** components are transformed as one rigid group in `v0.1`. Per-component pose is on the roadmap.
- **Huge files:** >200 MB or >200k faces will make the browser preview unhappy long before the kernel complains. Raise `ALIGNER_MAX_MB` and use the API.
- **Non-uniform scale** on STEP is legal but some downstream CAM tools silently ignore it — prefer uniform.
- **Units are your responsibility.** STEP is unit-annotated but exporters lie; the receipt records exactly what scale was applied.
- **Not a healing tool.** Self-intersections and gaps survive the round trip untouched.

---

## 🗺 Roadmap

- [x] STEP read / write round trip, transform baked into geometry
- [x] Principal-axis + bbox-centering auto-align
- [x] Interactive Dash viewport with neon wireframe
- [x] Transform receipt sidecar
- [x] 面清单 / 法向 / 面积（下拉拾取 + 3D 高亮）
- [ ] 3D 视口内直接点选面
- [ ] Per-component pose for assemblies
- [ ] IGES + BREP import
- [ ] Batch CLI: `aligner *.stp --mode principal -o out/`
- [ ] Docker image `ghcr.io/<org>/web-step-aligner` with OCC bundled
- [ ] STEP AP242 PMI preserved on export

---

## ❓ FAQ

**Why not just use FreeCAD?**
Use it — if you want a 900 MB application to rotate a bracket. This is 40 MB of Python and opens in a tab.

**Is my model uploaded anywhere?**
No. Everything runs in the process you started. No telemetry, no remote calls. Air-gap friendly.

**Why does the preview look different from the export?**
The preview is a tessellation (`BRepMesh`) with a tolerance; the export is exact BREP. Same pose, different fidelity.

**AP203 vs AP214 vs AP242?**
AP203 = geometry only, AP214 = geometry + colour/layers, AP242 = + PMI. Default is AP214; switch with `?ap=203|214|242`.

---

## 🤝 Contributing

```bash
git clone … && cd web-step-aligner
conda env create -f environment.yml
pre-commit install
pytest -q          # kernel tests run headless, no GUI needed
```

Issues with a minimal reproducer STEP file get fixed first. PRs that add a failing test alongside the fix get merged fastest.

## 📄 License

MIT © 2026 — do whatever you want, just don't blame us for your datum choices.
