[README.md](https://github.com/user-attachments/files/34635489/README.md)

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
![Version](https://img.shields.io/badge/version-0.2.0-39FF88)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen)](#contributing)

> A local web app that aligns, rotates and re-exports STEP files — without launching a CAD system.
>
> Runs on `127.0.0.1:8052`. No account, no upload, no licence server. Your geometry never leaves the machine.

</div>

---

<img width="1917" height="902" alt="Image" src="https://github.com/user-attachments/assets/9c9a3bfb-dd00-4a78-8d17-7419c44ade47" />

<p align="center"><i>Neon 3D viewport, live bounding box / volume / facet readout, and the transform panel — one page, no install.</i></p>

---

## ⚡ Why this exists

A vendor sends you a STEP file. It is beautiful. It is also:

- rotated 90° about an axis nobody documented,
- sitting 1,342.7 mm away from the origin,
- in millimetres while your assembly is in metres,
- and the only tool you own that can fix it takes **four minutes to launch** and **2 GB of RAM to rotate a box**.

Web-STEP-Aligner is a one-page cure: drop the file in the browser, look at it, snap it to a canonical pose (or rotate it by hand), and download a clean STEP with the transform **baked into the geometry** — not wrapped around it.

---

## 🔴 Demo

![Demo](demo.gif)
---

## 🧩 Features

| | Feature | What it actually does |
|---|---|---|
| 🌀 | **Nothing to install for the end user** | The whole tool is a local web page. Start one Python process, open a tab. No CAD seat, no plug-in, no licence. |
| 📦 | **Real STEP, real BREP** | Plain ISO-10303-21 is parsed and re-written on the way out (AP203 / AP214 / AP242). Faces, edges and assembly structure survive — no "export the tessellation as triangles" nonsense. |
| 🧠 | **OpenCASCADE inside a wheel** | Geometry comes from `cascadio`, which ships the same OCC that powers FreeCAD and KiCad StepUp — `pip install`, no conda, no OCCT build. |
| 🎯 | **One-click Auto Align** | Longest edge → X axis, thinnest face flat on XY with Z up, bounding-box centre moved to the origin. Deterministic, symmetric, repeatable. |
| 🎚️ | **Sliders for the last millimetre** | Translation in % of half the bounding box (100 % = half the model), rotation by any angle with degrees in a numeric box, plus one-click ±90° / ±180° increments and a reset. |
| 🧾 | **Numbers you can check** | Bounding box X×Y×Z, volume, BREP face count and triangle count are printed next to the viewport, so the pose is verified rather than trusted. |
| 💾 | **Transform baked in** | The export is a re-written STEP whose `CARTESIAN_POINT`s already carry the transform, plus a `.json` receipt: rotation matrix, translation, scale, pre/post bounding box. Auditable and diff-able in git. |
| 🌐 | **EN / 中文** | The entire UI is translated at runtime; switching language never resets the camera, the model or the sliders. |
| 🕶️ | **Cyberpunk skin** | Neon edges, dark grid, glowing thumbs. A CAE tool is allowed to look like a nightclub. |

> **Scope note:** this project only *poses* geometry. It does not heal topology, stitch gaps or rebuild surfaces — rigid and similarity transforms, nothing else.

---

## 🏗 Architecture

<p align="center">
  <img src="docs/architecture.png" alt="Architecture — browser, Dash callbacks, OCC kernel, STEP + receipt output" width="1000">
</p>

<p align="center"><i>Source of the diagram: <a href="docs/architecture.html">docs/architecture.html</a>.</i></p>

One process, one port, no database. The kernel is stateless and CPU-only; the only state is a temp directory per session under `jobs/<sid>/`, holding the uploaded STEP, the session transform and the mesh metadata.

Tessellation runs in a **subprocess with a hard timeout**, so a pathological BREP returns a readable error instead of freezing the server.

---

## 🚀 Quick start

### Requirements

- Python **3.10+** (developed and verified on 3.15)
- Windows 10/11, Linux or macOS
- **No conda, no pythonocc-core.** The kernel is `cascadio` (OpenCASCADE 8.0 inside the wheel); STEP reading and re-writing is handled by the ISO-10303-21 code in `aligner_core.py`.

```bash
git clone https://github.com/<your-name>/web-step-aligner.git
cd web-step-aligner
python -m pip install -r requirements.txt
```

`requirements.txt`

```txt
dash>=4.0
plotly>=5.20
flask>=3.0
numpy>=1.24
cascadio>=0.1.0      # OpenCASCADE tessellation + analytic face data (OCC bundled in the wheel)
```

### Run it

```bash
python app.py
# Windows: or double-click start_8052.bat  (installs deps, then opens the browser)
```

```
  ▸ Web-STEP-Aligner  v0.2.0
  ▸ kernel   cascadio / OpenCASCADE
  ▸ workdir  ./jobs
  ▸ ready    http://127.0.0.1:8052
```

Open **http://127.0.0.1:8052** → drop a `.step` / `.stp` (or click one of the samples) → align → export.

```bash
# optional environment overrides
export ALIGNER_PORT=8052        # Windows: set ALIGNER_PORT=8052
export ALIGNER_MAX_MB=200       # upload cap
export ALIGNER_DEBUG=1          # Dash debug mode
```

### Two engines, one piece of geometry

| Purpose | Implementation | Notes |
|---|---|---|
| Preview, measurement, face picking | `cascadio` → GLB (`include_brep=True`) | OCC tessellation plus per-face plane / cylinder parameters, used for face normals and face snapping. |
| Export | Pure-Python STEP re-write | Rewrites `CARTESIAN_POINT` / `DIRECTION` / `VECTOR` magnitudes / radius entities one by one, so the transform is **baked into the BREP** — no re-tessellation, no lost topology. |

> Known trap, read before touching the exporter: the `AXIS2_PLACEMENT_3D` inside each `*_SHAPE_REPRESENTATION` is interpreted by readers as the part frame and multiplied again. It must be preserved verbatim, otherwise the model gets transformed twice.

### Self-test

```bash
python tests/selftest.py      # headless: bbox / volume / minimal bbox / export round-trip / scale / face picking
```

---

## 🎛 Alignment modes

| Mode | Input | Use when |
|---|---|---|
| `principal` | — | Fastest "just make it straight": PCA + minimal bounding box, longest edge on X, thinnest face on XY. |
| `center` | `bbox` or `mass` | Re-origin only, keep the current orientation. |
| `face` | `from_face`, `to_face` | A mating face must sit flat on another plane. |
| `axis` | `face`, `axis` (X/Y/Z) | Put one planar face perpendicular to a world axis. |
| `transform` | `translate`, `rotate`, `scale`, `order` | You already know the numbers (the UI sliders use the same path). |

All modes compose with the scale field: `1.0` = keep, `25.4` = inch→mm, `0.001` = mm→m.

---

## 🔌 HTTP API

Everything the UI does is a plain endpoint — scriptable from CI, Blender or your CAE pre-processor.

| Method | Endpoint | Body / Query | Returns |
|---|---|---|---|
| `GET` | `/api/health` | — | `{ ok, version, kernel, workdir }` |
| `POST` | `/api/upload` | `multipart/form-data: file` | `{ sid, name, units, bbox, volume, faces, triangles }` |
| `POST` | `/api/align` | `{ sid, mode, params }` | `{ bbox, volume, matrix }` |
| `POST` | `/api/transform` | `{ sid, translate, rotate, scale, order }` | `{ bbox, volume, matrix }` |
| `GET` | `/api/mesh` | `?sid=` | tessellated preview + face inventory (JSON) |
| `GET` | `/api/export` | `?sid=` | `application/step` download (`<name>_aligned.step`) |
| `GET` | `/api/receipt` | `?sid=` | transform receipt JSON |
| `GET` | `/api/sessions` | — | recent sessions in the work directory |

```bash
# align a part and grab the result, headless
SID=$(curl -s -F file=@samples/lbracket.stp http://127.0.0.1:8052/api/upload | jq -r .sid)
curl -s -X POST http://127.0.0.1:8052/api/align \
     -H 'Content-Type: application/json' \
     -d "{\"sid\":\"$SID\",\"mode\":\"principal\"}" | jq
curl -s "http://127.0.0.1:8052/api/export?sid=$SID" -o lbracket_aligned.step
```

---

## ⚠️ Known limits

- **Assemblies:** components are transformed as one rigid group. Per-component pose is on the roadmap.
- **Huge files:** over the 200 MB cap, or a part with a few hundred thousand facets, will make the browser preview unhappy long before the kernel complains. Raise `ALIGNER_MAX_MB` and use the API.
- **Non-uniform scale** is legal in STEP but some downstream CAM tools silently ignore it — prefer uniform.
- **Units are your responsibility.** STEP is unit-annotated and exporters lie; the receipt records exactly what scale was applied.
- **Not a healing tool.** Self-intersections and gaps survive the round trip untouched.

---

## 🗺 Roadmap

- [x] STEP read / write round trip with the transform baked into the geometry
- [x] Auto-align: longest edge on X, thinnest face on XY, centre at the origin
- [x] Interactive Dash viewport with neon wireframe and live bounding box
- [x] Translate / rotate sliders with ±90° / ±180° increments and reset
- [x] Transform receipt (`.json` sidecar) on every export
- [x] Face inventory: normals, areas, dropdown picking and 3D highlight
- [x] EN / 中文 runtime switching, simple / expert layout
- [ ] Pick faces directly in the 3D viewport
- [ ] Per-component pose for assemblies
- [ ] IGES + BREP import
- [ ] Batch CLI: `aligner *.stp --mode principal -o out/`
- [ ] Docker image `ghcr.io/<org>/web-step-aligner` with OCC bundled
- [ ] AP242 PMI preserved on export

---

## ❓ FAQ

**Why not just use FreeCAD?**
Use it — if you want a 900 MB application to rotate a bracket. This is a few hundred lines of Python and it opens in a tab.

**Is my model uploaded anywhere?**
No. Everything runs in the process you started, on `127.0.0.1`. No telemetry, no remote calls. Air-gap friendly.

**Why does the preview look different from the export?**
The preview is a tessellation with a tolerance; the export is exact BREP. Same pose, different fidelity.

**AP203 vs AP214 vs AP242?**
AP203 = geometry only, AP214 = geometry + colour/layers, AP242 = + PMI. The writer keeps whatever entity set the input carried; the reader accepts all three.

**Why do the translate sliders show percentages?**
Because a percentage of the bounding box is scale-independent: 50 % means "half the model length" whether the part is 40 mm or 4 m.

---

## 🤝 Contributing

```bash
git clone https://github.com/<your-name>/web-step-aligner.git
cd web-step-aligner
python -m pip install -r requirements.txt
python tests/selftest.py     # kernel tests run headless, no GUI needed
```

Issues with a minimal reproducer STEP file get fixed first. PRs that add a failing test alongside the fix get merged fastest.

## 📄 License

MIT — see [LICENSE](LICENSE). Do whatever you want with it, just don't blame us for your datum choices.
