"""Generates deterministic sample images used by tests/ and stress_test.py.

Run once: python tools/generate_sample_images.py
Re-running overwrites the files with byte-identical content (fixed seed,
no randomness), so sharpness scores stay stable for assertions.
"""

import os

import numpy as np
from PIL import Image, ImageFilter

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_images")


def checkerboard(size=256, tile=16):
    arr = np.zeros((size, size), dtype=np.uint8)
    for y in range(0, size, tile):
        for x in range(0, size, tile):
            if ((x // tile) + (y // tile)) % 2 == 0:
                arr[y : y + tile, x : x + tile] = 255
    return Image.fromarray(arr, mode="L")


def noise_image(size=256, seed=0):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, size=(size, size), dtype=np.uint8)
    return Image.fromarray(arr, mode="L")


def stripes(size=256, width=4):
    arr = np.zeros((size, size), dtype=np.uint8)
    for x in range(0, size, width * 2):
        arr[:, x : x + width] = 255
    return Image.fromarray(arr, mode="L")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    sharp_sources = {
        "sharp_checkerboard.png": checkerboard(),
        "sharp_noise.png": noise_image(seed=42),
        "sharp_stripes.png": stripes(),
    }
    for name, img in sharp_sources.items():
        img.convert("RGB").save(os.path.join(OUT_DIR, name))

    # Blurry variants: heavy Gaussian blur collapses high-frequency edges,
    # driving Laplacian variance well below BLUR_THRESHOLD (100.0).
    for name, img in sharp_sources.items():
        blurry_name = name.replace("sharp_", "blurry_")
        blurred = img.filter(ImageFilter.GaussianBlur(radius=8))
        blurred.convert("RGB").save(os.path.join(OUT_DIR, blurry_name))

    # A flat/solid image: zero variance, unambiguously blurry.
    flat = Image.new("L", (256, 256), color=128)
    flat.convert("RGB").save(os.path.join(OUT_DIR, "blurry_flat.png"))

    print(f"wrote {len(sharp_sources) * 2 + 1} images to {OUT_DIR}")


if __name__ == "__main__":
    main()
