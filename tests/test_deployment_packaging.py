"""Static guards for production Docker dependency and build-context boundaries."""
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_installs_only_declared_project_runtime_dependencies():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    runtime_names = {item.split("[")[0].split(">=")[0].split("==")[0].lower() for item in project["dependencies"]}

    assert "RUN pip install --no-cache-dir ." in dockerfile
    assert "-r requirements.txt" not in dockerfile
    assert not runtime_names.intersection({"pytest", "pytest-asyncio", "httpx2", "ruff"})


def test_docker_context_excludes_internal_reports_and_dev_files_but_keeps_readme():
    ignored = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        "tests",
        "notes",
        "research",
        "diary",
        "requirements.txt",
        "AGENT_PROGRESS.md",
        "MEMORA_PHASE2_BUG_REPORT.md",
        "MEMORA_DIARY.md",
    } <= ignored
    assert "README.md" not in ignored  # pyproject.toml requires it during `pip install .`
