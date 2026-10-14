"""Keep packaged resources separate from persistent application data."""
import os
import sys
from pathlib import Path


RESOURCE_ROOT = Path(__file__).resolve().parent.parent


def default_data_root():
    configured = os.getenv("RESUME_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "简历工作台数据"
    shared = RESOURCE_ROOT.parent / "简历工作台数据"
    return shared if shared.exists() else RESOURCE_ROOT / "data"
