"""Headless self-test for aligner_core — no browser, no server.

    python tests/selftest.py

Checks the three things that actually matter:
  1. STEP parse + tessellation reproduce the known bbox / volume of the samples.
  2. The exported STEP, read back through the kernel, is exactly the posed model
     (proves the transform is baked into the BREP, not wrapped).
  3. Face snapping / axis alignment produce finite rigid transforms.
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import aligner_core as ac  # noqa: E402

FAIL = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAIL.append(label)


def load(name: str):
    path = os.path.join(ROOT, "samples", name)
    doc = ac.StepDoc.load(path)
    mesh = ac.tessellate(path, unit_label=doc.unit_label, units_per_metre=doc.units_per_metre)
    return doc, mesh


def main() -> int:
    print("== STEP parse / tessellate ==")
    doc, box = load("box.stp")
    size = box.bbox()["size"]
    check("box bbox = 120 x 60 x 25 mm",
          np.allclose(size, [120, 60, 25], atol=1e-3), f"{np.round(size, 3).tolist()}")
    check("box volume = 180000 mm3", abs(box.volume() - 180000) < 1.0, f"{box.volume():.1f}")
    check("box has 6 planar faces",
          len([f for f in box.faces if f["type"] == "plane"]) == 6)
    check("box feature edges = 12", len(box.edges) == 12, str(len(box.edges)))

    doc2, lb = load("lbracket.stp")
    check("lbracket volume (2 solids) = 108000 mm3", abs(lb.volume() - 108000) < 2.0, f"{lb.volume():.1f}")
    check("lbracket keeps 12 distinct BREP faces",
          len({f["face_index"] for f in lb.faces}) == 12)

    print("== principal alignment ==")
    tf = ac.align_principal(lb)
    aligned = lb.transformed(tf)
    asize = aligned.bbox()["size"]
    check("minimal bbox = 100 x 92 x 30 mm",
          np.allclose(sorted(asize, reverse=True), [100, 92, 30], atol=1e-3),
          f"{np.round(asize, 3).tolist()}")
    check("bbox centred on origin", np.allclose(aligned.bbox()["center"], 0, atol=1e-6))
    check("rotation is orthonormal (det=+1)",
          abs(np.linalg.det(tf.R) - 1) < 1e-9, f"det={np.linalg.det(tf.R):.9f}")

    print("== export round trip (transform baked into BREP) ==")
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "aligned.step")
    with open(out, "w", encoding="latin-1") as f:
        f.write(doc2.transformed_text(tf))
    d3 = ac.StepDoc.load(out)
    m3 = ac.tessellate(out, unit_label=d3.unit_label, units_per_metre=d3.units_per_metre)
    check("re-read bbox == aligned bbox",
          np.allclose(sorted(m3.bbox()["size"], reverse=True), sorted(asize, reverse=True), atol=1e-3),
          f"{np.round(m3.bbox()['size'], 3).tolist()}")
    check("re-read volume preserved", abs(m3.volume() - lb.volume()) < 1.0,
          f"{m3.volume():.1f}")
    check("no baked-in node transform (geometry only)",
          all("matrix" not in n for n in (ac._glb_load(out + ".glb")[0].get("nodes") or [{}])))

    print("== scale ==")
    s = 25.4
    outS = os.path.join(tmp, "scaled.step")
    with open(outS, "w", encoding="latin-1") as f:
        f.write(doc.transformed_text(ac.Transform(s=s)))
    mS = ac.tessellate(outS, unit_label="mm", units_per_metre=1000)
    check("scale 25.4 multiplies bbox by 25.4",
          np.allclose(mS.bbox()["size"], np.array([120, 60, 25]) * s, atol=0.05),
          f"{np.round(mS.bbox()['size'], 2).tolist()}")
    check("scale 25.4 multiplies volume by 25.4^3",
          abs(mS.volume() - 180000 * s ** 3) / (180000 * s ** 3) < 1e-6)

    print("== face picking ==")
    inv = ac.face_inventory(box)
    check("face inventory lists 6 planes", len(inv) == 6)
    t1 = ac.snap_faces(box, inv[0]["id"], inv[-1]["id"])
    check("snap_faces returns a rigid transform",
          abs(np.linalg.det(t1.R) - 1) < 1e-9 and np.all(np.isfinite(t1.t)))
    t2 = ac.align_to_axis(box, inv[0]["id"], "Z")
    m2 = box.transformed(t2)
    check("align_to_axis seats the picked face on z=0",
          abs(m2.bbox()["min"][2]) < 1e-6, f"min z={m2.bbox()['min'][2]:.6f}")

    print()
    if FAIL:
        print(f"{len(FAIL)} check(s) FAILED: {FAIL}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
