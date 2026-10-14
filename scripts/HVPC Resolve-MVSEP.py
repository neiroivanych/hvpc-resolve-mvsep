"""HVPC Resolve-MVSEP menu launcher. Copy into Resolve's Scripts/Utility; see README."""

# When copying this file, set the checkout folder below, for example:
# PROJECT_ROOT = r"D:\Projects\Resolve-MVSEP"
# Alternatively set the RESOLVE_MVSEP_HOME environment variable and restart Resolve.
PROJECT_ROOT = ""


def _launch_resolve_mvsep(injected_resolve: object = None) -> int:
    import os
    import sys
    import traceback
    from pathlib import Path

    try:
        configured = os.environ.get("RESOLVE_MVSEP_HOME") or PROJECT_ROOT
        if configured:
            root = Path(configured).expanduser().resolve()
        elif "__file__" in globals():
            root = Path(__file__).resolve().parents[1]
        else:
            raise RuntimeError("Set PROJECT_ROOT in this launcher or RESOLVE_MVSEP_HOME.")
        source_directory = root / "src"
        if not (source_directory / "resolve_mvsep" / "main.py").is_file():
            raise RuntimeError(
                "HVPC Resolve-MVSEP checkout not found. Set PROJECT_ROOT in the copied "
                "launcher to the project folder containing src, or set RESOLVE_MVSEP_HOME."
            )
        sys.path.insert(0, str(source_directory))
        try:
            from resolve_mvsep.main import run

            return run(root, injected_resolve)
        finally:
            sys.path.remove(str(source_directory))
    except Exception:
        print("HVPC Resolve-MVSEP M1 launcher failed. Open Workspace > Console (Python 3).")
        traceback.print_exc()
        return 1


# Resolve menu scripts execute in the host's globals; do not call sys.exit there.
# The documented injected `resolve` object is preferred to an external connection.
_launch_resolve_mvsep(globals().get("resolve"))
