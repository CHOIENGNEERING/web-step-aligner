"""aligner_core — geometry kernel + STEP (ISO-10303-21) manipulation for Web-STEP-Aligner.

Two independent engines live here on purpose:

  1. **Preview / measurement engine** — OpenCASCADE (bundled inside the `cascadio`
     wheel, no conda needed). STEP -> GLB tessellation, plus analytic plane data
     (`include_brep=True`) used for face snapping and face inventory.

  2. **Export engine** — a pure-python reader/rewriter for the STEP text file. The
     transform is baked by rewriting every `CARTESIAN_POINT`, `DIRECTION`, `VECTOR`
     magnitude and radius-bearing entity in place. No re-tessellation, no topology
     loss: the output is the *same* BREP, merely posed differently.

Authored for Python 3.10+, Windows/Linux/macOS. No OCC API is touched from here
except through `cascadio`, which keeps this file importable without a CAD stack.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

# cascadio is optional at import time: the text-level STEP engine still works without it.
try:  # pragma: no cover - environment dependent
    import cascadio  # type: ignore

    HAVE_CASCADIO = True
except Exception:  # pragma: no cover
    cascadio = None  # type: ignore
    HAVE_CASCADIO = False


# --------------------------------------------------------------------------------------
# 4x4 / rigid transform helpers.  Convention:  p' = s * (R @ p) + t
# --------------------------------------------------------------------------------------
@dataclass
class Transform:
    R: np.ndarray = field(default_factory=lambda: np.eye(3))
    t: np.ndarray = field(default_factory=lambda: np.zeros(3))
    s: float = 1.0

    def copy(self) -> "Transform":
        return Transform(self.R.copy(), self.t.copy(), float(self.s))

    def compose(self, other: "Transform") -> "Transform":
        """`other` applied *after* self: p'' = other(self(p))."""
        return Transform(
            R=other.R @ self.R,
            t=other.R @ (self.s * self.t) + other.t,
            s=self.s * other.s,
        )

    def apply(self, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, dtype=float)
        return self.s * (pts @ self.R.T) + self.t

    def apply_dir(self, dirs: np.ndarray) -> np.ndarray:
        return np.asarray(dirs, dtype=float) @ self.R.T

    def matrix4(self) -> list[list[float]]:
        """Row-major 4x4 with the scale folded in as s*R (uniform scale matrix)."""
        m = self.s * self.R
        return [[float(m[0, 0]), float(m[0, 1]), float(m[0, 2]), float(self.t[0])],
                [float(m[1, 0]), float(m[1, 1]), float(m[1, 2]), float(self.t[1])],
                [float(m[2, 0]), float(m[2, 1]), float(m[2, 2]), float(self.t[2])],
                [0.0, 0.0, 0.0, 1.0]]

    def to_dict(self) -> dict[str, Any]:
        return {"matrix": self.matrix4(), "rotation": self.R.tolist(),
                "translation": self.t.tolist(), "scale": float(self.s)}

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> "Transform":
        if not d:
            return Transform()
        return Transform(R=np.array(d["rotation"], dtype=float),
                         t=np.array(d["translation"], dtype=float),
                         s=float(d.get("scale", 1.0)))

    def is_identity(self, tol: float = 1e-12) -> bool:
        return (abs(self.s - 1.0) < tol and np.allclose(self.R, np.eye(3), atol=tol)
                and np.allclose(self.t, 0.0, atol=tol))


def rot_x(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)


def rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=float)


def rot_z(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


def euler_matrix(rx_deg: float, ry_deg: float, rz_deg: float, order: str = "XYZ") -> np.ndarray:
    mats = {"X": rot_x(math.radians(rx_deg)), "Y": rot_y(math.radians(ry_deg)),
            "Z": rot_z(math.radians(rz_deg))}
    R = np.eye(3)
    for ch in order.upper():          # intrinsic-free: post-multiply in the given order
        R = R @ mats[ch]
    return R


def rotation_from_vectors(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Minimal rotation taking unit vector a onto unit vector b (Rodrigues)."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    a = a / (np.linalg.norm(a) or 1.0)
    b = b / (np.linalg.norm(b) or 1.0)
    v = np.cross(a, b)
    c = float(np.clip(np.dot(a, b), -1.0, 1.0))
    if np.linalg.norm(v) < 1e-12:
        if c > 0:
            return np.eye(3)
        # 180 deg: rotate about any axis perpendicular to a
        p = np.array([1.0, 0.0, 0.0])
        if abs(a[0]) > 0.9:
            p = np.array([0.0, 1.0, 0.0])
        axis = np.cross(a, p)
        axis /= np.linalg.norm(axis)
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]], dtype=float)
    return np.eye(3) + K + K @ K * ((1 - c) / (np.linalg.norm(v) ** 2))


# --------------------------------------------------------------------------------------
# STEP text engine
# --------------------------------------------------------------------------------------
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?"
_TUPLE_RE = re.compile(r"\(\s*(" + _NUM + r")\s*,\s*(" + _NUM + r")\s*(?:,\s*(" + _NUM + r")\s*)?\)")
_STAT_RE = re.compile(r"#(\d+)\s*=\s*")

RADIUS_LAST = {"CIRCLE", "CYLINDRICAL_SURFACE", "SPHERICAL_SURFACE", "CONICAL_SURFACE",
               "ELLIPSE", "PARABOLA", "HYPERBOLA", "POLYLINE"}  # POLYLINE handled separately
TWO_RADII = {"TOROIDAL_SURFACE"}
SKIP_POINT_TYPES = {"CARTESIAN_POINT_2D_REJECT"}  # placeholder for clarity


def _split_args(s: str) -> list[str]:
    """Split a STEP argument list on top-level commas (strings / nested parens aware)."""
    out, depth, cur, in_str = [], 0, [], False
    for ch in s:
        if in_str:
            cur.append(ch)
            if ch == "'":
                in_str = False
            continue
        if ch == "'":
            in_str = True
            cur.append(ch)
        elif ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return out


def _to_float(tok: str) -> float:
    return float(tok.replace("D", "E").replace("d", "e"))


def _fmt(x: float) -> str:
    if not math.isfinite(x):
        return "0."
    if abs(x) < 1e-12:
        x = 0.0
    s = f"{x:.10g}"
    if "e" in s or "E" in s:
        s = f"{x:.12f}".rstrip("0")
    if "." not in s:
        s += "."
    return s


@dataclass
class StepDoc:
    """A parsed STEP file: header text + ordered entity statements + unit info."""

    raw: str
    header: str
    statements: dict[int, str] = field(default_factory=dict)  # id -> full "TYPE(...)" body
    order: list[int] = field(default_factory=list)
    units_per_metre: float = 1000.0  # model units per metre (1000 = millimetre)
    unit_label: str = "mm"

    # ---- parsing -------------------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike) -> "StepDoc":
        with open(path, "r", encoding="latin-1", errors="replace") as f:
            raw = f.read()
        return cls.parse(raw)

    @classmethod
    def parse(cls, raw: str) -> "StepDoc":
        if "\x00" in raw:                      # 二进制 / 被截断的伪 STEP
            raw = raw.replace("\x00", "")
        m = re.search(r"\bDATA\s*;", raw, re.IGNORECASE)
        if not m:
            raise ValueError("不是有效的 STEP 文本：找不到 DATA 段"
                             "（需要 ISO-10303-21 的 .step/.stp；"
                             ".igs/.iges/.stl/.obj/.x_t 请先在 CAD 里转成 STEP）")
        header = raw[:m.end()]
        body_start = m.end()
        end = re.search(r"\bENDSEC\s*;", raw[body_start:], re.IGNORECASE)
        body = raw[body_start: body_start + (end.start() if end else len(raw) - body_start)]

        doc = cls(raw=raw, header=header)
        for stmt in cls._statements(body):
            mm = _STAT_RE.match(stmt)
            if not mm:
                continue
            eid = int(mm.group(1))
            rest = stmt[mm.end():].strip()
            doc.statements[eid] = rest
            doc.order.append(eid)
        doc._detect_units()
        doc._find_rep_placements()
        return doc

    def _find_rep_placements(self) -> None:
        """CARTESIAN_POINT / DIRECTION ids belonging to a shape-representation placement.

        Readers (OpenCASCADE among them) treat the representation's own
        AXIS2_PLACEMENT_3D as the part frame and compose it onto the geometry, so
        these must stay put in the file or the transform is applied twice.
        """
        self.skip_ids: set[int] = set()
        for eid in self.order:
            if not self.etype(eid).endswith("SHAPE_REPRESENTATION"):
                continue
            args = _split_args(self.statements[eid][self.statements[eid].find("(") + 1:
                                                     self.statements[eid].rfind(")")])
            if len(args) < 2:
                continue
            for ref in re.findall(r"#(\d+)", args[1]):
                rid = int(ref)
                if self.etype(rid) != "AXIS2_PLACEMENT_3D":
                    continue
                self.skip_ids.add(rid)
                for sub in re.findall(r"#(\d+)", self.statements[rid]):
                    self.skip_ids.add(int(sub))

    @staticmethod
    def _statements(body: str) -> list[str]:
        out, cur, depth, in_str = [], [], 0, False
        for ch in body:
            if in_str:
                cur.append(ch)
                if ch == "'":
                    in_str = False
                continue
            if ch == "'":
                in_str = True
                cur.append(ch)
            elif ch == "(":
                depth += 1
                cur.append(ch)
            elif ch == ")":
                depth -= 1
                cur.append(ch)
            elif ch == ";" and depth == 0:
                stmt = "".join(cur).strip()
                if stmt:
                    out.append(stmt)
                cur = []
            else:
                cur.append(ch)
        tail = "".join(cur).strip()
        if tail:
            out.append(tail)
        return out

    def _detect_units(self) -> None:
        t = self.raw.upper()
        if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", t) or re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH\b", t):
            self.units_per_metre, self.unit_label = 1.0 / 0.0254, "in"
        elif re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'FOOT'", t):
            self.units_per_metre, self.unit_label = 1.0 / 0.3048, "ft"
        elif re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'MILLIMETRE'|CONVERSION_BASED_UNIT\s*\(\s*'MILLIMETER'", t):
            self.units_per_metre, self.unit_label = 1000.0, "mm"
        elif re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'CENTIMETRE'|CONVERSION_BASED_UNIT\s*\(\s*'CENTIMETER'", t):
            self.units_per_metre, self.unit_label = 100.0, "cm"
        elif re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'METRE'|CONVERSION_BASED_UNIT\s*\(\s*'METER'", t):
            self.units_per_metre, self.unit_label = 1.0, "m"
        else:
            # SI_UNIT(.MILLI.,.METRE.) / (.$,.METRE.) fallbacks
            if re.search(r"SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.", t):
                self.units_per_metre, self.unit_label = 1000.0, "mm"
            elif re.search(r"SI_UNIT\s*\(\s*\.CENTI\.\s*,\s*\.METRE\.", t):
                self.units_per_metre, self.unit_label = 100.0, "cm"
            else:
                self.units_per_metre, self.unit_label = 1000.0, "mm"

    # ---- queries -------------------------------------------------------------
    def etype(self, eid: int) -> str:
        body = self.statements.get(eid, "")
        k = body.find("(")
        return body[:k].strip().upper() if k > 0 else ""

    def points(self) -> dict[int, np.ndarray]:
        out = {}
        for eid in self.order:
            if self.etype(eid) == "CARTESIAN_POINT":
                args = _split_args(self.statements[eid][len("CARTESIAN_POINT("):-1])
                if len(args) >= 2:
                    mm = _TUPLE_RE.search(args[1])
                    if mm:
                        v = [_to_float(g) for g in mm.groups() if g is not None]
                        out[eid] = np.array(v, dtype=float)
        return out

    def vertex_points(self) -> np.ndarray | None:
        """Tessellation-free point cloud fallback (used when cascadio is unavailable)."""
        pts = []
        vset = {eid for eid in self.order if self.etype(eid) == "VERTEX_POINT"}
        allp = self.points()
        for eid in vset:
            mm = re.search(r"#(\d+)", self.statements[eid])
            if mm:
                p = allp.get(int(mm.group(1)))
                if p is not None:
                    pts.append(p)
        if not pts:
            return None
        return np.array(pts, dtype=float)

    def counts(self) -> dict[str, int]:
        d: dict[str, int] = {}
        for eid in self.order:
            k = self.etype(eid)
            d[k] = d.get(k, 0) + 1
        return d

    # ---- rewriting -----------------------------------------------------------
    def transformed_text(self, tf: Transform) -> str:
        """Bake `tf` into every geometric entity and return new STEP text."""
        R, t, s = tf.R, tf.t, float(tf.s)
        rotate_only = abs(s - 1.0) < 1e-12
        use_rot = not np.allclose(R, np.eye(3), atol=1e-13)

        pieces: list[str] = [self.header]
        skip = getattr(self, "skip_ids", set())
        for eid in self.order:
            body = self.statements[eid]
            new_body = body if eid in skip else \
                self._transform_entity(eid, body, R, t, s, use_rot, rotate_only)
            pieces.append(f"#{eid} = {new_body};\n")
        pieces.append("ENDSEC;\nEND-ISO-10303-21;\n")
        return "".join(pieces)

    def _transform_entity(self, eid: int, body: str, R, t, s: float,
                          use_rot: bool, rotate_only: bool) -> str:
        k = body.find("(")
        if k <= 0:
            return body
        etype = body[:k].strip().upper()
        args = _split_args(body[k + 1: body.rfind(")")])
        if not args:
            return body

        if etype in ("CARTESIAN_POINT", "CARTESIAN_POINT_2D"):
            idx = 1 if len(args) > 1 else 0
            m = _TUPLE_RE.search(args[idx])
            if m:
                vals = [_to_float(g) for g in m.groups() if g is not None]
                p = np.zeros(3)
                p[: len(vals)] = vals
                q = tf_apply_point(p, R, t, s)
                new = "(" + ",".join(_fmt(float(x)) for x in q[: len(vals)]) + ")"
                args[idx] = _TUPLE_RE.sub(new, args[idx], count=1)
                return f"{body[:k]}({','.join(args)})"
            return body

        if etype == "DIRECTION":
            idx = 1 if len(args) > 1 else 0
            m = _TUPLE_RE.search(args[idx])
            if m and use_rot:
                vals = [_to_float(g) for g in m.groups() if g is not None]
                d = np.zeros(3)
                d[: len(vals)] = vals
                d2 = R @ d
                n0, n1 = np.linalg.norm(d), np.linalg.norm(d2)
                if n0 > 1e-15 and n1 > 1e-15:
                    d2 = d2 * (n0 / n1)
                new = "(" + ",".join(_fmt(float(x)) for x in d2[: len(vals)]) + ")"
                args[idx] = _TUPLE_RE.sub(new, args[idx], count=1)
                return f"{body[:k]}({','.join(args)})"
            return body

        if not rotate_only:
            if etype == "VECTOR" and len(args) >= 3:
                try:
                    args[2] = _fmt(_to_float(args[2]) * s)
                    return f"{body[:k]}({','.join(args)})"
                except ValueError:
                    return body
            if etype in RADIUS_LAST - {"POLYLINE"} and len(args) >= 2:
                try:
                    args[-1] = _fmt(_to_float(args[-1]) * s)
                    return f"{body[:k]}({','.join(args)})"
                except ValueError:
                    return body
            if etype in TWO_RADII and len(args) >= 4:
                try:
                    args[2] = _fmt(_to_float(args[2]) * s)
                    args[3] = _fmt(_to_float(args[3]) * s)
                    return f"{body[:k]}({','.join(args)})"
                except ValueError:
                    return body
        return body


def tf_apply_point(p: np.ndarray, R: np.ndarray, t: np.ndarray, s: float) -> np.ndarray:
    return s * (R @ p) + t


# --------------------------------------------------------------------------------------
# Tessellation (cascadio / OpenCASCADE) + GLB decoding
# --------------------------------------------------------------------------------------
@dataclass
class Mesh:
    V: np.ndarray                  # (n,3) vertices, model units
    T: np.ndarray                  # (m,3) triangle indices
    tri_face: np.ndarray           # (m,) face index per triangle (-1 unknown)
    faces: list[dict[str, Any]]    # analytic face records from cascadio (planes / cylinders …)
    edges: np.ndarray              # (e,2) feature-edge vertex pairs, model units
    unit_label: str = "mm"

    def bbox(self) -> dict[str, list[float]]:
        lo, hi = self.V.min(axis=0), self.V.max(axis=0)
        return {"min": lo.tolist(), "max": hi.tolist(), "size": (hi - lo).tolist(),
                "center": ((lo + hi) / 2.0).tolist()}

    def volume(self) -> float:
        if len(self.T) == 0:
            return 0.0
        a, b, c = self.V[self.T[:, 0]], self.V[self.T[:, 1]], self.V[self.T[:, 2]]
        return float(abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0))

    def area_centroid(self) -> tuple[np.ndarray, float]:
        if len(self.T) == 0:
            return self.V.mean(axis=0), 0.0
        a, b, c = self.V[self.T[:, 0]], self.V[self.T[:, 1]], self.V[self.T[:, 2]]
        cross = np.cross(b - a, c - a)
        areas = 0.5 * np.linalg.norm(cross, axis=1)
        tot = float(areas.sum())
        if tot <= 0:
            return self.V.mean(axis=0), 0.0
        cen = ((a + b + c) / 3.0 * areas[:, None]).sum(axis=0) / tot
        return cen, tot

    def transformed(self, tf: Transform) -> "Mesh":
        return Mesh(V=tf.apply(self.V), T=self.T, tri_face=self.tri_face,
                    faces=_xf_faces(self.faces, tf), edges=tf.apply(self.edges),
                    unit_label=self.unit_label)


def _xf_faces(faces: list[dict[str, Any]], tf: Transform) -> list[dict[str, Any]]:
    out = []
    for f in faces:
        g = dict(f)
        if "origin" in f:
            g["origin"] = tf.apply(np.array([f["origin"]]))[0].tolist()
        if "normal" in f:
            nn = tf.apply_dir(np.array([f["normal"]]))[0]
            nrm = np.linalg.norm(nn)
            g["normal"] = (nn / nrm).tolist() if nrm > 1e-12 else nn.tolist()
        if "x_dir" in f:
            xx = tf.apply_dir(np.array([f["x_dir"]]))[0]
            nrm = np.linalg.norm(xx)
            g["x_dir"] = (xx / nrm).tolist() if nrm > 1e-12 else xx.tolist()
        for key in ("extent_x", "extent_y"):
            if key in f and isinstance(f[key], list):
                g[key] = [float(v) * tf.s for v in f[key]]
        out.append(g)
    return out


_CT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


def _glb_load(path: str) -> tuple[dict, bytes]:
    data = open(path, "rb").read()
    magic, _ver, _total = struct.unpack("<III", data[:12])
    if magic != 0x46546C67:
        raise ValueError("不是 GLB 文件")
    off, js, bin_ = 12, None, b""
    while off < len(data):
        ln, ty = struct.unpack("<II", data[off: off + 8])
        chunk = data[off + 8: off + 8 + ln]
        if ty == 0x4E4F534A:
            js = json.loads(chunk.decode("utf-8"))
        elif ty == 0x004E4942:
            bin_ = chunk
        off += 8 + ln
    if js is None:
        raise ValueError("GLB 缺少 JSON chunk")
    return js, bin_


def _accessor(js: dict, bin_: bytes, idx: int) -> np.ndarray:
    a = js["accessors"][idx]
    bv = js["bufferViews"][a["bufferView"]]
    dtype, size = _CT[a["componentType"]]
    n = _NCOMP[a["type"]]
    base = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride") or n * size
    count = a["count"]
    if stride == n * size:
        arr = np.frombuffer(bin_, dtype=np.dtype("<" + dtype), count=count * n, offset=base)
    else:
        raw = np.frombuffer(bin_, dtype=np.uint8, count=stride * (count - 1) + n * size, offset=base)
        idxs = (np.arange(count) * stride)[:, None] + np.arange(n * size)[None, :]
        arr = raw[idxs].reshape(-1).view(np.dtype("<" + dtype))
    return arr.reshape(count, n) if n > 1 else arr


def _node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=float).reshape(4, 4).T  # column-major
    T = np.eye(4)
    if "translation" in node:
        T[:3, 3] = node["translation"]
    R = np.eye(3)
    if "rotation" in node:
        x, y, z, w = node["rotation"]
        R = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    S = np.eye(3)
    if "scale" in node:
        S = np.diag(node["scale"])
    T[:3, :3] = R @ S
    return T


def _collect_nodes(js: dict) -> list[tuple[int, np.ndarray]]:
    out: list[tuple[int, np.ndarray]] = []
    nodes = js.get("nodes", [])
    scenes = js.get("scenes", [])
    roots = scenes[js.get("scene", 0)]["nodes"] if scenes else range(len(nodes))

    def walk(i: int, parent: np.ndarray):
        node = nodes[i]
        M = parent @ _node_matrix(node)
        if "mesh" in node:
            out.append((node["mesh"], M))
        for ch in node.get("children", []):
            walk(ch, M)

    for r in roots:
        walk(r, np.eye(4))
    return out


def _feature_edges(V: np.ndarray, T: np.ndarray, tri_face: np.ndarray,
                   angle_deg: float = 25.0) -> np.ndarray:
    """Crease/boundary edges from the tessellation — the neon wireframe."""
    if len(T) == 0:
        return np.zeros((0, 2), dtype=float)
    key = np.round(V, 6)
    # weld duplicate vertices (cascadio splits them per face)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    Ti = inv[T]
    tri = Ti.astype(np.int64)
    e = np.vstack([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    owner = np.tile(np.arange(len(tri)), 3)
    a, b = np.minimum(e[:, 0], e[:, 1]), np.maximum(e[:, 0], e[:, 1])
    pair = a * (len(uniq) + 1) + b
    order = np.argsort(pair, kind="stable")
    pair_s, a_s, b_s, own_s = pair[order], a[order], b[order], owner[order]

    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    ln = np.linalg.norm(n, axis=1)
    ln[ln == 0] = 1.0
    n = n / ln[:, None]

    bounds = np.flatnonzero(np.diff(pair_s)) + 1
    groups = np.split(np.arange(len(pair_s)), bounds)
    cos_thr = math.cos(math.radians(angle_deg))
    keep: list[int] = []          # positional indices into pair_s / a_s / b_s
    for g in groups:
        if len(g) != 2:
            keep.append(int(g[0]))            # boundary or non-manifold -> draw
            continue
        f0, f1 = int(own_s[g[0]]), int(own_s[g[1]])
        same_face = tri_face is not None and tri_face[f0] == tri_face[f1]
        if not same_face:
            keep.append(int(g[0]))            # crease between two BREP faces
        elif float(np.dot(n[f0], n[f1])) < cos_thr:
            keep.append(int(g[0]))            # sharp crease inside one face
    if not keep:
        return np.zeros((0, 2), dtype=float)
    keep = np.array(sorted(set(keep)), dtype=int)
    return uniq[np.column_stack([a_s[keep], b_s[keep]])]


def tessellate(step_path: str, tol_linear: float = 0.002, tol_angular: float = 0.35,
               unit_label: str = "mm", units_per_metre: float = 1000.0) -> Mesh:
    """STEP -> Mesh in *model units* (cascadio emits metres, we convert back).

    Robustness (每条都是真实踩过的坑)：
      * cascadio/OCCT 打不开非 ASCII 的 Windows 路径 —— 中文文件名会让 step_to_glb
        静默失败（stderr 打 "Cannot open input file"，不抛异常、也不生成 glb）。
        所以一律先复制进 ASCII 临时目录再解析。
      * 愈合壳体 / 薄板 / 勺子这类模型在默认容差下可能一片面都三角化不出来，需要
        逐级放宽容差重试；最后一次关掉 include_brep 兜底（此时没有解析面数据，
        但至少能看见、能量、能摆正）。
      * TM_brep_faces.faces 允许出现 None（OCCT 没解析出解析面）；必须按位置占位，
        否则 face_index 会和 faceIndices 错位，面高亮/贴合全部错乱。
    """
    if not HAVE_CASCADIO:
        raise RuntimeError("cascadio 未安装：pip install cascadio（用于 STEP 曲面三角化）")

    work = tempfile.mkdtemp(prefix="aligner_tess_")
    try:
        src = os.path.join(work, "src.step")
        try:
            shutil.copyfile(str(step_path), src)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"无法读取 STEP 文件：{exc}") from exc

        attempts = [
            (float(tol_linear), float(tol_angular), True, True),
            (max(float(tol_linear), 0.01), max(float(tol_angular), 0.50), True, False),
            (max(float(tol_linear), 0.05), max(float(tol_angular), 0.70), False, False),
        ]
        glb, err = None, None
        for i, (tl, ta, brep, par) in enumerate(attempts):
            g = os.path.join(work, f"out{i}.glb")
            try:
                cascadio.step_to_glb(src, g, tol_linear=tl, tol_angular=ta,
                                     include_brep=brep, merge_primitives=True,
                                     use_parallel=par)
            except Exception as exc:  # noqa: BLE001
                err = exc
                continue
            if os.path.exists(g) and os.path.getsize(g) > 128:
                glb = g
                break
            err = err or RuntimeError("cascadio 没有生成 GLB")
        if glb is None:
            raise RuntimeError(f"cascadio 三角化失败：{err} · 该文件可能不是实体几何"
                               "（纯线框 / 未封闭曲面 / 不是 STEP 文本）")

        js, bin_ = _glb_load(glb)
        k = float(units_per_metre)  # metres -> model units (mm file -> x1000)
        Vs, Ts, face_of_tri, face_recs = [], [], [], []
        tri_offset = 0
        for mesh_idx, M in _collect_nodes(js):
            mesh = js["meshes"][mesh_idx]
            for prim in mesh.get("primitives", []):
                attrs = prim.get("attributes", {})
                if "POSITION" not in attrs:
                    continue
                P = _accessor(js, bin_, attrs["POSITION"]).astype(float)
                Ph = np.hstack([P, np.ones((len(P), 1))])
                P = (Ph @ M.T)[:, :3] * k
                idx = _accessor(js, bin_, prim["indices"]) if "indices" in prim else \
                    np.arange(len(P), dtype=np.uint32)
                idx = idx.reshape(-1, 3).astype(np.int64)
                ext = (prim.get("extensions") or {}).get("TM_brep_faces")
                ftri = np.full(len(idx), -1, dtype=np.int64)
                if ext:
                    base = len(face_recs)      # global face ids: primitives reuse 0..n per part
                    for i_fr, fr in enumerate(ext.get("faces") or []):
                        if not isinstance(fr, dict):
                            # OCCT 未解析出解析面：占位，保持 face_index 对齐
                            face_recs.append({"face_index": base + i_fr, "type": "unknown"})
                            continue
                        g = {"face_index": base + int(fr.get("face_index", i_fr)),
                             "type": fr.get("type", "?")}
                        for key in ("origin", "normal", "x_dir", "extent_x", "extent_y"):
                            if key in fr:
                                v = fr[key]
                                if key in ("origin",):
                                    v = (np.array([v[0], v[1], v[2], 1.0]) @ M.T)[:3] * k
                                    g[key] = v.tolist()
                                elif key in ("normal", "x_dir"):
                                    g[key] = (M[:3, :3] @ np.array(v, float)).tolist()
                                else:
                                    g[key] = [float(x) * k for x in v]
                        face_recs.append(g)
                    if "faceIndices" in ext:
                        fi = _accessor(js, bin_, ext["faceIndices"]).reshape(-1).astype(np.int64)
                        if len(fi) == len(idx):
                            ftri = fi + base
                        elif len(fi) == len(P):
                            ftri = fi[idx[:, 0]] + base
                Vs.append(P)
                Ts.append(idx + tri_offset)
                face_of_tri.append(ftri)
                tri_offset += len(P)
        if not Vs:
            raise ValueError("STEP 中没有可三角化的实体（可能是纯线框或空文件）")
        V = np.vstack(Vs)
        T = np.vstack(Ts)
        if len(T) == 0 or len(V) == 0:
            raise ValueError("三角化结果为空：该模型没有实体面（纯线框 / 曲面片）")
        tri_face = np.concatenate(face_of_tri)
        edges = _feature_edges(V, T, tri_face)
        return Mesh(V=V, T=T, tri_face=tri_face, faces=face_recs, edges=edges,
                    unit_label=unit_label)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------------------
# Out-of-process tessellation (crash + hang containment)
# --------------------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
TESS_TIMEOUT = float(os.environ.get("ALIGNER_TESS_TIMEOUT", "15"))
MAX_TRIS = int(os.environ.get("ALIGNER_MAX_TRIS", "600000"))


class TessellationError(RuntimeError):
    """STEP read / triangulation failed, timed out, or the kernel died."""


_CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import numpy as np
import aligner_core as ac
step, out_npz, unit, upm, rep = sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5]), sys.argv[6]
try:
    m = ac.tessellate(step, unit_label=unit, units_per_metre=upm)
    np.savez_compressed(
        out_npz, V=m.V, T=m.T, tri_face=m.tri_face, edges=m.edges,
        faces=np.array(json.dumps(m.faces), dtype=object),
        unit=np.array([m.unit_label]),
    )
    with open(rep, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ok": True}, ensure_ascii=False))
except BaseException as exc:
    try:
        with open(rep, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                               ensure_ascii=False))
    except Exception:
        pass
    sys.exit(3)
"""


def tessellate_isolated(step_path: str, unit_label: str = "mm",
                        units_per_metre: float = 1000.0,
                        timeout: float | None = None) -> Mesh:
    """Run :func:`tessellate` in a child process with a hard wall-clock limit.

    Why: cascadio/OCCT can hang on self-intersecting BREP or hard-crash the
    interpreter (segfault) on very heavy models.  Called straight from a Dash
    callback that shows up in the browser as "nothing happens, no red text".
    Parsing out-of-process turns both into ordinary, reportable errors.
    """
    limit = float(TESS_TIMEOUT if timeout is None else timeout)
    work = tempfile.mkdtemp(prefix="aligner_iso_")
    try:
        out_npz = os.path.join(work, "mesh.npz")
        rep = os.path.join(work, "result.json")
        cmd = [sys.executable, "-c", _CHILD, HERE, os.fspath(step_path),
               out_npz, str(unit_label), repr(float(units_per_metre)), rep]
        kw: dict[str, Any] = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
        try:
            _, err_b = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            raise TessellationError(f"解析超时（>{limit:.0f} s）") from None

        info: dict[str, Any] = {}
        if os.path.exists(rep):
            try:
                with open(rep, encoding="utf-8") as f:
                    info = json.load(f)
            except Exception:  # noqa: BLE001
                info = {}
        if not info.get("ok"):
            tail = (err_b or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
            detail = info.get("error") or ("解析进程异常退出（返回码 %s）"
                                           % proc.returncode)
            if tail and not info.get("error"):
                detail = f"{detail} · " + " | ".join(tail)
            # 子进程把异常类型也写进 error 了；用户看到的应该是「原因」，不是 "RuntimeError: "。
            detail = re.sub(r"^(?:RuntimeError|Exception|ValueError|OSError):\s*", "", detail)
            raise TessellationError(detail)
        if not os.path.exists(out_npz):
            raise TessellationError("解析进程没有产出网格数据")
        z = np.load(out_npz, allow_pickle=True)
        faces = json.loads(str(z["faces"])) if z["faces"].size else []
        unit = str(z["unit"][0]) if z["unit"].size else unit_label
        return Mesh(V=z["V"], T=z["T"], tri_face=z["tri_face"], faces=faces,
                    edges=z["edges"], unit_label=unit)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _tessellate_checked(step_path: str, doc: "StepDoc") -> Mesh:
    """Isolated tessellation + a payload sanity ceiling (never returns a partial mesh)."""
    mesh = tessellate_isolated(step_path, unit_label=doc.unit_label,
                               units_per_metre=doc.units_per_metre)
    if len(mesh.T) > MAX_TRIS:
        raise TessellationError(
            f"三角片数量 {len(mesh.T):,} 超过上限 {MAX_TRIS:,}：几何体过于复杂")
    if mesh.V is None or len(mesh.V) == 0 or len(mesh.T) == 0:
        raise TessellationError("几何体没有可用的三角面片（可能全是破面或空实体）")
    return mesh


# --------------------------------------------------------------------------------------
# Measurement / alignment
# --------------------------------------------------------------------------------------
def _bbox_volume_in_frame(mesh: Mesh, R: np.ndarray) -> tuple[float, np.ndarray]:
    P = mesh.V @ R.T
    lo, hi = P.min(axis=0), P.max(axis=0)
    return float(np.prod(np.maximum(hi - lo, 1e-9))), (lo + hi) / 2.0


def _orthonormal_frame(n1: np.ndarray, n2: np.ndarray) -> np.ndarray | None:
    n1 = n1 / (np.linalg.norm(n1) or 1.0)
    v = n2 - np.dot(n2, n1) * n1
    if np.linalg.norm(v) < 1e-6:
        return None
    n2 = v / np.linalg.norm(v)
    R = np.array([n1, n2, np.cross(n1, n2)])
    return R if np.linalg.det(R) > 0 else np.array([n1, -n2, -np.cross(n1, n2)])


def principal_frame(mesh: Mesh) -> tuple[np.ndarray, np.ndarray]:
    """Return (R, center) mapping the part onto a canonical pose.

    Candidates = PCA frame (area-weighted) + every orthonormal triad that can be
    built from significant planar face normals; the one with the smallest
    axis-aligned bbox wins. Axes are then sorted longest -> X, thinnest -> Z and
    the bbox centre goes to the origin (not the centroid: a bracket is lopsided).
    """
    a, b, c = mesh.V[mesh.T[:, 0]], mesh.V[mesh.T[:, 1]], mesh.V[mesh.T[:, 2]]
    areas = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    cents = (a + b + c) / 3.0
    w = areas / max(areas.sum(), 1e-30)
    mu = (cents * w[:, None]).sum(axis=0)
    d = cents - mu
    cov = (w[:, None, None] * d[:, :, None] * d[:, None, :]).sum(axis=0)
    evals, evecs = np.linalg.eigh(cov)
    axes = evecs[:, np.argsort(evals)[::-1]].T
    axes[2] = np.cross(axes[0], axes[1])
    candidates: list[np.ndarray] = [axes]

    normals: list[np.ndarray] = []
    total_area = sum(abs((f.get("extent_x") or [0, 0])[1] - (f.get("extent_x") or [0, 0])[0]) *
                     abs((f.get("extent_y") or [0, 0])[1] - (f.get("extent_y") or [0, 0])[0])
                     for f in mesh.faces if f.get("type") == "plane")
    for f in mesh.faces:
        if f.get("type") != "plane":
            continue
        ex, ey = f.get("extent_x") or [0, 0], f.get("extent_y") or [0, 0]
        area = abs((ex[1] - ex[0]) * (ey[1] - ey[0]))
        if total_area > 0 and area < 0.02 * total_area:
            continue
        n = np.array(f["normal"], float)
        nn = n / (np.linalg.norm(n) or 1.0)
        if not any(abs(abs(np.dot(nn, m)) - 1.0) < 1e-6 for m in normals):
            normals.append(nn)
        if len(normals) >= 8:
            break
    for i in range(len(normals)):
        for j in range(i + 1, len(normals)):
            F = _orthonormal_frame(normals[i], normals[j])
            if F is not None:
                candidates.append(F)
        for ax in axes:                     # one picked face + a PCA axis
            F = _orthonormal_frame(normals[i], ax)
            if F is not None:
                candidates.append(F)

    best_R, best_vol, best_center = axes, float("inf"), np.zeros(3)
    for R in candidates:
        vol, cen = _bbox_volume_in_frame(mesh, R)
        if vol < best_vol - 1e-9:
            best_R, best_vol, best_center = R, vol, cen

    R = best_R
    # deterministic signs: largest |component| of each axis is positive
    for i in range(3):
        j = int(np.argmax(np.abs(R[i])))
        if R[i, j] < 0:
            R[i] = -R[i]
    if np.linalg.det(R) < 0:
        R[2] = -R[2]
    # sort axes by extent, longest first, keep right-handed
    P = mesh.V @ R.T
    ext = P.max(axis=0) - P.min(axis=0)
    R = R[np.argsort(ext)[::-1]]
    if np.linalg.det(R) < 0:
        R[1] = -R[1]
    if np.linalg.det(R) < 0:
        R[2] = -R[2]
    P = mesh.V @ R.T
    center = (P.max(axis=0) + P.min(axis=0)) / 2.0
    return R, center


def align_principal(mesh: Mesh) -> Transform:
    R, center = principal_frame(mesh)
    return Transform(R=R, t=-center, s=1.0)


def align_center(mesh: Mesh, mode: str = "bbox") -> Transform:
    if mode == "mass":
        cen, _ = mesh.area_centroid()
    else:
        bb = mesh.bbox()
        cen = np.array(bb["center"])
    return Transform(R=np.eye(3), t=-cen, s=1.0)


def snap_faces(mesh: Mesh, from_face: int, to_face: int) -> Transform:
    """Rotate so `from_face` normal opposes `to_face` normal, then seat the planes."""
    planes = {f.get("face_index"): f for f in mesh.faces if f.get("type") == "plane"}
    fa, fb = planes.get(from_face), planes.get(to_face)
    if fa is None or fb is None:
        raise ValueError("选中的面不是可用的平面（需要在 face 列表里选 plane 类型的面）")
    na = np.array(fa["normal"], float)
    nb = np.array(fb["normal"], float)
    R = rotation_from_vectors(na, -nb)
    oa = np.array(fa["origin"], float)
    ob = np.array(fb["origin"], float)
    # after rotation, plane a passes through R@oa; slide it onto plane b along nb
    pa = R @ oa
    delta = float(np.dot(ob - pa, nb))
    t = ob - (pa + delta * nb)
    return Transform(R=R, t=t, s=1.0)


def align_to_axis(mesh: Mesh, face_id: int, axis: str = "Z", keep_side: bool = True) -> Transform:
    """Rotate a picked planar face so its normal points along -axis (sits on that datum)."""
    planes = {f.get("face_index"): f for f in mesh.faces if f.get("type") == "plane"}
    f = planes.get(face_id)
    if f is None:
        raise ValueError("选中的面不是平面")
    n = np.array(f["normal"], float)
    k = {"X": 0, "Y": 1, "Z": 2}[axis.upper()]
    target = np.zeros(3)
    target[k] = 1.0
    R = rotation_from_vectors(n, -target if keep_side else target)
    P = mesh.V @ R.T
    lo, hi = P.min(axis=0), P.max(axis=0)
    new_o = R @ np.array(f["origin"], float)
    t = -(lo + hi) / 2.0
    t[k] = -lo[k] if abs(new_o[k] - lo[k]) <= abs(new_o[k] - hi[k]) else -hi[k]
    return Transform(R=R, t=t, s=1.0)


def face_inventory(mesh: Mesh, limit: int = 60) -> list[dict[str, Any]]:
    """Planar faces sorted by area (largest first) for the face-picking dropdowns."""
    out: list[dict[str, Any]] = []
    for f in mesh.faces:
        if f.get("type") != "plane":
            continue
        ex = f.get("extent_x") or [0, 0]
        ey = f.get("extent_y") or [0, 0]
        area = abs((ex[1] - ex[0]) * (ey[1] - ey[0]))
        n = np.array(f["normal"], float)
        out.append({
            "id": f.get("face_index"),
            "type": "plane",
            "area": float(area),
            "origin": f.get("origin"),
            "normal": f["normal"],
            "normal_str": f"({n[0]:+.2f}, {n[1]:+.2f}, {n[2]:+.2f})",
            "size_str": f"{abs(ex[1] - ex[0]):.1f} × {abs(ey[1] - ey[0]):.1f}",
        })
    out.sort(key=lambda d: -d["area"])
    return out[:limit]


# --------------------------------------------------------------------------------------
# Session store on disk: jobs/<sid>/
# --------------------------------------------------------------------------------------
def new_sid() -> str:
    return time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:10]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Project:
    """One uploaded model: source STEP + tessellation cache + cumulative transform."""

    def __init__(self, workdir: str, sid: str):
        self.dir = os.path.join(workdir, sid)
        self.sid = sid
        self.src = os.path.join(self.dir, "src.step")
        self.meta_path = os.path.join(self.dir, "meta.json")
        self.mesh_cache = os.path.join(self.dir, "mesh.npz")
        self.name = "model.step"
        self.transform = Transform()
        self.mesh: Mesh | None = None
        self.doc: StepDoc | None = None
        self.history: list[dict[str, Any]] = []

    # ---- lifecycle -----------------------------------------------------------
    @classmethod
    def create(cls, workdir: str, filename: str, payload: bytes) -> "Project":
        sid = new_sid()
        os.makedirs(os.path.join(workdir, sid), exist_ok=True)
        p = cls(workdir, sid)
        with open(p.src, "wb") as f:
            f.write(payload)
        p.name = filename
        p.doc = StepDoc.load(p.src)
        p.mesh = _tessellate_checked(p.src, p.doc)
        p._save_mesh()
        p.save()
        return p

    @classmethod
    def open(cls, workdir: str, sid: str) -> "Project":
        d = os.path.join(workdir, sid)
        if not os.path.isdir(d):
            raise FileNotFoundError(f"session {sid} not found")
        p = cls(workdir, sid)
        if os.path.exists(p.meta_path):
            meta = json.load(open(p.meta_path, encoding="utf-8"))
            p.name = meta.get("name", p.name)
            p.transform = Transform.from_dict(meta.get("transform"))
            p.history = meta.get("history", [])
        p.doc = StepDoc.load(p.src)
        if os.path.exists(p.mesh_cache):
            p.mesh = p._load_mesh()
        else:
            p.mesh = _tessellate_checked(p.src, p.doc)
            p._save_mesh()
        return p

    def _save_mesh(self) -> None:
        m = self.mesh
        np.savez_compressed(
            self.mesh_cache, V=m.V, T=m.T, tri_face=m.tri_face, edges=m.edges,
            faces=np.array(json.dumps(m.faces), dtype=object), unit=np.array([m.unit_label]),
        )

    def _load_mesh(self) -> Mesh:
        z = np.load(self.mesh_cache, allow_pickle=True)
        faces = json.loads(str(z["faces"])) if z["faces"].size else []
        return Mesh(V=z["V"], T=z["T"], tri_face=z["tri_face"], faces=faces,
                    edges=z["edges"], unit_label=str(z["unit"][0]) if z["unit"].size else "mm")

    def save(self) -> None:
        meta = {"sid": self.sid, "name": self.name, "created": time.time(),
                "transform": self.transform.to_dict(), "history": self.history[-50:]}
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)

    # ---- ops -----------------------------------------------------------------
    def apply(self, op: Transform, label: str = "op") -> Transform:
        self.transform = self.transform.compose(op)
        self.history.append({"label": label, "ts": time.time(),
                             "op": op.to_dict(), "cumulative": self.transform.to_dict()})
        self.save()
        return self.transform

    def set_transform(self, tf: Transform, label: str = "set") -> Transform:
        self.transform = tf
        self.history.append({"label": label, "ts": time.time(),
                             "cumulative": self.transform.to_dict()})
        self.save()
        return tf

    def reset(self) -> Transform:
        return self.set_transform(Transform(), "reset")

    # ---- output --------------------------------------------------------------
    def export_bytes(self) -> bytes:
        text = self.doc.transformed_text(self.transform)
        return text.encode("latin-1", errors="replace")

    def receipt(self) -> dict[str, Any]:
        m0, m1 = self.mesh, self.mesh.transformed(self.transform)
        u = self.mesh.unit_label
        return {
            "tool": "Web-STEP-Aligner v0.1.0",
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "session": self.sid,
            "source": {"name": self.name, "sha256": sha256_file(self.src),
                       "bytes": os.path.getsize(self.src), "units": u},
            "transform": self.transform.to_dict(),
            "bbox_before": m0.bbox(),
            "bbox_after": m1.bbox(),
            "volume_before": m0.volume(),
            "volume_after": m1.volume(),
            "faces": len(self.mesh.faces),
            "triangles": int(len(self.mesh.T)),
            "history": self.history[-50:],
        }
