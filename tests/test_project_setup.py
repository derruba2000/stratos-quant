from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).parents[1]


def test_epic_one_poetry_dependencies_are_declared():
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    runtime_dependencies = pyproject["tool"]["poetry"]["dependencies"]
    assert runtime_dependencies["python"] == "^3.12"
    assert {
        "lib-pybroker",
        "gradio",
        "pandas",
        "numpy",
        "sqlalchemy",
        "requests",
        "python-dotenv",
    } <= runtime_dependencies.keys()

    dev_dependencies = pyproject["tool"]["poetry"]["group"]["dev"]["dependencies"]
    assert "pytest" in dev_dependencies
    assert "pytest" not in runtime_dependencies


def test_poetry_lockfile_is_present():
    assert (PROJECT_ROOT / "poetry.lock").is_file()
