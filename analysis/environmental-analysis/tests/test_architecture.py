"""Architecture guardrails.

The repository's rule is ``apps -> analysis`` and never the reverse. These
tests fail loudly if a web framework or a UI dependency is ever imported from
anywhere under ``analysis/``, because such an import would quietly make the
analysis layer un-runnable from a CLI, a notebook or a different frontend.
"""

import ast
import unittest
from pathlib import Path

from tests.support import PROJECT_DIR

ANALYSIS_ROOT = PROJECT_DIR.parent
FORBIDDEN_ROOTS = {
    "streamlit",
    "flask",
    "django",
    "fastapi",
    "dash",
    "gradio",
    "tornado",
    "bottle",
    "pyramid",
    "starlette",
    "altair",
}

SKIP_DIRECTORIES = {".venv", "venv", "__pycache__", ".git", "node_modules"}


def python_files(root):
    for path in sorted(Path(root).rglob("*.py")):
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        yield path


def imported_roots(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


class ArchitectureTests(unittest.TestCase):
    def test_this_subsystem_imports_no_web_framework(self):
        offenders = []
        for path in python_files(PROJECT_DIR):
            forbidden = imported_roots(path) & FORBIDDEN_ROOTS
            if forbidden:
                offenders.append(f"{path}: {sorted(forbidden)}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_whole_analysis_tree_imports_no_web_framework(self):
        offenders = []
        for path in python_files(ANALYSIS_ROOT):
            forbidden = imported_roots(path) & FORBIDDEN_ROOTS
            if forbidden:
                offenders.append(f"{path}: {sorted(forbidden)}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_engine_never_imports_the_apps_layer(self):
        """Prose may mention the app; code may not import it."""
        app_modules = {"apps", "services", "app_pages", "components", "styles"}
        offenders = []
        for path in python_files(PROJECT_DIR):
            forbidden = imported_roots(path) & app_modules
            if forbidden:
                offenders.append(f"{path}: {sorted(forbidden)}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_engine_never_writes_to_stdout_outside_the_cli(self):
        allowed = {"cli.py"}
        offenders = []
        for path in python_files(PROJECT_DIR / "environmental"):
            if path.name in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                ):
                    offenders.append(f"{path}:{node.lineno}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_engine_raises_no_system_exit(self):
        """A library must not decide to end the caller's process.

        Checked on the syntax tree, so a docstring explaining why the
        reliability audit's ``SystemExit``-raising loader is deliberately not
        reused does not count as an offence.
        """
        allowed = {"cli.py"}
        offenders = []
        for path in python_files(PROJECT_DIR / "environmental"):
            if path.name in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "SystemExit":
                    offenders.append(f"{path}:{node.lineno}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_matplotlib_is_only_imported_by_the_plotting_module(self):
        offenders = []
        for path in python_files(PROJECT_DIR / "environmental"):
            if path.name == "plots.py":
                continue
            if "matplotlib" in imported_roots(path):
                offenders.append(str(path))
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_importing_the_package_pulls_in_no_plotting_stack(self):
        """Checked in a clean interpreter.

        Asserting against this process's ``sys.modules`` would depend on
        whether some earlier test happened to import matplotlib.
        """
        import subprocess
        import sys

        script = (
            "import sys; import environmental; "
            "print('matplotlib' in sys.modules); print(environmental.ENGINE_VERSION)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            check=True,
        )
        loaded, version = result.stdout.split()
        self.assertEqual(loaded, "False")
        self.assertTrue(version)


if __name__ == "__main__":
    unittest.main()
