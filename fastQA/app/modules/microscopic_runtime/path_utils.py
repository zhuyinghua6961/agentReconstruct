from __future__ import annotations

import os
from pathlib import Path


def resolve_project_path(path_value: str, project_root: str | Path) -> str:
    if os.path.isabs(path_value):
        return os.path.normpath(path_value)
    return os.path.normpath(os.path.join(str(project_root), path_value))
