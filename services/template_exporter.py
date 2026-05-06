"""
Export helpers for video edit results.
"""
import os
import zipfile


# ============================================================
# Zip Export Helper
# ============================================================

def zip_directory(source_dir: str, output_zip: str) -> str:
    """Zip a directory for download."""
    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.path.dirname(source_dir))
                zf.write(file_path, arcname)
    return output_zip


# ============================================================
# Helpers
# ============================================================

def _is_image(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif")
