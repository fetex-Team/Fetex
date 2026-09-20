"""저장소 전체에서 공유하는 경로 계약."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_path(*parts: str | Path) -> Path:
    """저장소 기준의 안전한 절대 경로를 반환한다."""
    return PROJECT_ROOT.joinpath(*map(str, parts))
