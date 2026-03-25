from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def sdk_root(project_root: Path | None = None) -> Path:
    package_root = Path(__file__).resolve().parent
    vendored = package_root / "_vendor" / "sdk"
    if (vendored / "python").exists():
        return vendored
    if project_root is not None:
        return project_root / "docs" / "sdk"
    raise RuntimeError("Unable to locate bundled SDK runtime assets")


def sdk_python_dir(project_root: Path | None = None) -> Path:
    return sdk_root(project_root) / "python"


def _load_sdk_runtime_setup(sdk_python_dir: Path) -> ModuleType:
    runtime_setup_path = sdk_python_dir / "_runtime_setup.py"
    spec = importlib.util.spec_from_file_location("_sdk_runtime_setup", runtime_setup_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load SDK runtime setup module from {runtime_setup_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ensure_runtime_package_installed(python_executable: str, sdk_python_dir: Path) -> Any:
    module = _load_sdk_runtime_setup(sdk_python_dir)
    return module.ensure_runtime_package_installed(python_executable, sdk_python_dir)
