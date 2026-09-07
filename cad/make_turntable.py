"""360-degree turntable of the HarvestGuard pod, for the repository README.

Transparent background, so the render sits on light or dark GitHub themes alike.

Renders one frame per 10 degrees of azimuth from the same STL the sizing and
thermal models describe, then writes an animated GIF. Materials match the build:
white polyurethane panel, monocrystalline module, galvanised steel frame.
"""
from __future__ import annotations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from stl import mesh

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "cad"
FRAMES = 36
SIZE = 560


def shaded(tris, rgb, k1=0.55, amb=0.42):
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n /= (np.linalg.norm(n, axis=1, keepdims=True) + 1e-12)
    n = np.round(n, 2)
    n /= (np.linalg.norm(n, axis=1, keepdims=True) + 1e-12)
    key = np.array([0.45, -0.70, 0.56]); key /= np.linalg.norm(key)
    fill = np.array([-0.55, 0.35, 0.28]); fill /= np.linalg.norm(fill)
    lit = np.clip(n @ key, 0, 1) * k1 + np.clip(n @ fill, 0, 1) * 0.22 + amb
    return np.clip(lit[:, None] * np.array(rgb)[None, :] * 1.10, 0, 1)


def main() -> None:
    tris = mesh.Mesh.from_file(ROOT / "cad" / "harvestguard_pod.stl").vectors
    z = tris[:, :, 2].mean(axis=1)
    lo, hi = z.min(), z.max()
    panel, base = z > lo + 0.86 * (hi - lo), z < lo + 0.05 * (hi - lo)
    body = ~(panel | base)

    pts = tris.reshape(-1, 3)
    plo, phi = pts.min(0), pts.max(0)
    ext = phi - plo
    pad = ext * 0.04

    frames = []
    for i in range(FRAMES):
        azim = -180 + i * (360 / FRAMES)
        fig = plt.figure(figsize=(SIZE / 100, SIZE / 100), dpi=100)
        fig.patch.set_alpha(0.0)
        ax = fig.add_subplot(111, projection="3d")
        for mask, rgb in ((body, (0.925, 0.929, 0.925)),
                          (panel, (0.106, 0.145, 0.220)),
                          (base, (0.478, 0.510, 0.537))):
            if not mask.any():
                continue
            sub = tris[mask]
            coll = Poly3DCollection(sub, facecolors=shaded(sub, rgb),
                                    edgecolors="none", linewidths=0, zsort="min")
            coll.set_sort_zpos(None)
            ax.add_collection3d(coll)
        for setter, c in ((ax.set_xlim, 0), (ax.set_ylim, 1), (ax.set_zlim, 2)):
            setter(plo[c] - pad[c], phi[c] + pad[c])
        ax.set_box_aspect(tuple(ext / ext.max()))
        ax.view_init(elev=20, azim=azim)
        ax.set_axis_off()
        fig.subplots_adjust(0, 0, 1, 1)

        ax.patch.set_alpha(0.0)
        fig.canvas.draw()
        rgba = np.asarray(fig.canvas.buffer_rgba()).copy()

        # Quantise the opaque pixels only, then reserve one palette slot for
        # transparency so the GIF sits on any background.
        alpha = rgba[:, :, 3]
        rgb = Image.fromarray(rgba[:, :, :3])
        pal = rgb.convert("P", palette=Image.ADAPTIVE, colors=255)
        pal.paste(255, mask=Image.fromarray((alpha < 128).astype(np.uint8) * 255))
        pal.info["transparency"] = 255
        frames.append(pal)
        plt.close(fig)
        print(f"  frame {i + 1:2d}/{FRAMES}  azim {azim:+.0f}")

    gif = OUT / "harvestguard_turntable.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:],
                   duration=90, loop=0, optimize=False,
                   transparency=255, disposal=2)
    print(f"\nwrote {gif}  ({gif.stat().st_size / 1e6:.2f} MB, {FRAMES} frames)")


if __name__ == "__main__":
    main()
