from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PERMISSION_STEP = "RUN chmod -R a+rX /app/backend /app/worker /app/radar_db /app/deploy"


@pytest.mark.parametrize("path", ["backend/Dockerfile", "worker/Dockerfile"])
def test_python_images_make_copied_runtime_readable_before_dropping_root(path):
    dockerfile = (ROOT / path).read_text(encoding="utf-8")
    assert PERMISSION_STEP in dockerfile
    assert dockerfile.rindex("COPY ") < dockerfile.index(PERMISSION_STEP)
    assert dockerfile.index(PERMISSION_STEP) < dockerfile.index("USER 10001:10001")
