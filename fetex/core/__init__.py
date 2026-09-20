"""공통 설정과 저장소 경로."""

from .config import CFG, DEFAULT_CONFIG, REGION_PRESETS, load_config
from .paths import PROJECT_ROOT, project_path

__all__ = ["CFG", "DEFAULT_CONFIG", "PROJECT_ROOT", "REGION_PRESETS", "load_config", "project_path"]
