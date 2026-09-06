import cv2

# Variance of the Laplacian below this value is classified as "blurry".
# Chosen empirically for typical photo-sized images (see DESIGN.md).
BLUR_THRESHOLD = 100.0


def compute_blur(image_path: str) -> dict:
    """Read image_path as grayscale and score its sharpness.

    Raises FileNotFoundError for OpenCV's failure-to-decode case
    (cv2.imread returns None instead of raising), which the worker turns
    into a failed/retried job.
    """
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)  # pylint: disable=no-member
    if img is None:
        raise FileNotFoundError(f"cannot read image: {image_path}")

    variance = cv2.Laplacian(img, cv2.CV_64F).var()  # pylint: disable=no-member
    is_blurry = variance < BLUR_THRESHOLD
    return {"is_blurry": bool(is_blurry), "sharpness_score": round(float(variance), 4)}
