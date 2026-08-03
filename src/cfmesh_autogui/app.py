"""CFMesh-AutoGUI application entry point.

Configures logging (before any module imports to capture init errors),
shows a branded splash, applies the design system theme, and launches
the main window.
"""
import logging
import sys

logger = logging.getLogger(__name__)

# Windows' console defaults to cp1252, which can't encode plenty of
# characters this app's own log messages use (arrows, checkmarks, the
# multiplication sign in "Domain: 3.000 x 0.255 x 0.255 m", etc.).
# logging's own emit() catches the resulting UnicodeEncodeError and just
# dumps a traceback instead of the actual message — confirmed live: an
# arrow in a healing-summary log line ("faces 12790→12776") produced
# a "--- Logging error ---" block instead of the message, discarding the
# real log content and cluttering the console with an unrelated-looking
# traceback. reconfigure() (Python 3.7+) switches the stream to UTF-8
# without needing to touch every individual log call site.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except (AttributeError, ValueError):
    pass  # non-reconfigurable stream (e.g. redirected to something unusual)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

import os as _os
from logging.handlers import RotatingFileHandler as _RotatingFileHandler
_log_dir = _os.path.join(_os.environ.get("APPDATA", _os.path.expanduser("~")), "cfmesh-autogui", "logs")
_os.makedirs(_log_dir, exist_ok=True)
_file_handler = _RotatingFileHandler(
    _os.path.join(_log_dir, "app.log"), maxBytes=5*1024*1024, backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_file_handler)

import sys as _sys

# Fix: add OCP DLL directory to PATH so cadquery/OCP native libraries load
try:
    import OCP as _ocp
    _ocp_dir = _os.path.dirname(_ocp.__file__)
    if _ocp_dir not in _os.environ.get("PATH", ""):
        _os.environ["PATH"] = _ocp_dir + _os.pathsep + _os.environ.get("PATH", "")
except Exception:
    pass  # OCP not yet installed — will fail later with clear message

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QSplashScreen

# MainWindow is deliberately NOT imported here at module level. Importing it
# pulls in cadquery/OCP, trimesh, pyvista, pyvistaqt, and gmsh transitively —
# measured live: ~4.2s combined on a typical machine, out of ~5.5s total
# startup. Since this file's top-level imports all run before main() (and
# therefore before QApplication/the splash screen exist), that entire cost
# used to happen with literally nothing on screen — no window, no splash,
# nothing — before everything appeared at once. main() now creates and shows
# the splash screen FIRST, then imports MainWindow: same total startup time,
# but the user sees immediate feedback instead of an app that looks frozen
# or not launched at all for 4+ seconds.
from cfmesh_autogui.gui.theme import (
    apply_theme, set_theme_mode, current_mode, current_request,
)
from cfmesh_autogui.gui.design_tokens import APP_NAME, APP_VERSION
from cfmesh_autogui.gui.branding import (
    make_splash_pixmap, apply_app_icon, make_app_icon,
)


def _init_i18n(app):
    from pathlib import Path
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    locale = QLocale(QLocale.Italian)
    translator = QTranslator()
    paths = [
        Path(__file__).resolve().parent.parent / "locale" / "it" / "LC_MESSAGES",
        Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)),
    ]
    for p in paths:
        if translator.load(locale, "cfmesh_autogui", ".", str(p)):
            app.installTranslator(translator)
            break


def _load_plugins(app):
    from pathlib import Path
    import importlib.util
    import sys as _sys

    # project root = app.py / src / cfmesh_autogui / .. = src/../.. = project root
    project_root = Path(__file__).resolve().parent.parent.parent
    plugins_dir = project_root / "plugins"
    if not plugins_dir.is_dir():
        return

    logger = logging.getLogger(__name__)
    _sys.path.insert(0, str(project_root))
    from plugins.plugin_base import Plugin

    for f in sorted(plugins_dir.glob("*.py")):
        if f.name.startswith("_") or f.name == "plugin_base.py":
            continue
        mod_name = f"plugins.{f.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, f)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for attr_name in dir(mod):
                cls = getattr(mod, attr_name)
                if isinstance(cls, type) and issubclass(cls, Plugin) and cls is not Plugin:
                    instance = cls()
                    instance.on_install(app)
                    logger.info("Plugin loaded: %s.%s", mod_name, cls.__name__)
        except Exception as e:
            logger.error("Failed to load plugin %s: %s", f.name, e)


def main():
    from cfmesh_autogui import __version__

    # FeatureDetectWorker isolates GMSH's feature-detection pass in a
    # child process (a hard native crash there must never take the whole
    # app down — see feature_detector.py). In a normal dev run it invokes
    # `sys.executable -m cfmesh_autogui.core.feature_detector ...`, but in
    # a PyInstaller-frozen build sys.executable IS this same exe, and the
    # frozen bootloader does not support the `-m` flag — it just re-runs
    # this same main() regardless of arguments, which opened a second GUI
    # window that immediately crashed (confirmed live: a second
    # "CFMesh-AutoGUI.exe" process appeared with an "Unhandled exception
    # in script" title every time feature detection ran). Detect that
    # exact invocation shape here and dispatch to the CLI entry point
    # instead of ever reaching QApplication.
    if len(_sys.argv) > 1 and _sys.argv[1] == "--feature-detect":
        from cfmesh_autogui.core.feature_detector import _main as _feature_detect_main
        _sys.exit(_feature_detect_main(_sys.argv[2:]))

    if len(_sys.argv) > 1 and _sys.argv[1] == "--watertight":
        from cfmesh_autogui.core.geometry_repair import _main as _watertight_main
        _sys.exit(_watertight_main(_sys.argv[2:]))

    # Same frozen-exe dispatch problem as --feature-detect/--watertight
    # above, for the three GMSH subprocess launches in openfoam_runner.py
    # (GmshSurfaceWorker, GmshVolumeWorker, and the msh->foam conversion
    # in main_window.py): _gmsh_wrapper_script_cmd's frozen branch has
    # always assumed these flags land here, but nothing ever actually
    # dispatched them — confirmed by direct inspection, not yet hit in
    # practice since this app hasn't shipped as a frozen build. GMSH's
    # __main__ block (not refactored into a _main(argv) like the other
    # two) reads its subcommand from sys.argv[1], so it's restored here
    # before handing off via runpy — args[1:] (already args, minus the
    # leading "surface"/"volume"/"convert_to_foam" word) is what the
    # frozen cmd carries after this flag, per _gmsh_wrapper_script_cmd.
    _GMSH_FLAG_SUBCOMMAND = {
        "--gmsh-surface": "surface",
        "--gmsh-volume": "volume",
        "--gmsh-convert-to-foam": "convert_to_foam",
    }
    if len(_sys.argv) > 1 and _sys.argv[1] in _GMSH_FLAG_SUBCOMMAND:
        # Frozen (PyInstaller) build: gmsh_wrapper.py lives inside the PYZ
        # archive, so runpy.run_path on its source path cannot work (the
        # file does not exist on disk).  Call the CLI function directly —
        # this is the ONLY subprocess entry that reaches GMSH volume
        # meshing in the frozen exe, so it must not depend on disk layout.
        from cfmesh_autogui.core import gmsh_wrapper
        subcommand = _GMSH_FLAG_SUBCOMMAND[_sys.argv[1]]
        _sys.argv = [_sys.argv[0], subcommand] + _sys.argv[2:]
        gmsh_wrapper._cli_main(_sys.argv)
        _sys.exit(0)

    if len(_sys.argv) > 1 and _sys.argv[1] in ("--help", "-h"):
        print(f"CFMesh-AutoGUI v{__version__} — OpenFOAM mesh preprocessor")
        print()
        print("Usage: CFMesh-AutoGUI [options]")
        print()
        print("Options:")
        print("  --help, -h         Show this help message and exit")
        print("  --version, -v      Show version and exit")
        print("  --feature-detect   Run GMSH feature detection (subprocess mode)")
        print("  --watertight       Run watertight check/repair (subprocess mode)")
        print("  --gmsh-surface     Run GMSH surface STL generation (subprocess mode)")
        print("  --gmsh-volume      Run GMSH volume mesh generation (subprocess mode)")
        print("  --gmsh-convert-to-foam  Convert GMSH .msh to OpenFOAM polyMesh (subprocess mode)")
        print()
        print("Without arguments, the GUI application starts.")
        _sys.exit(0)

    if len(_sys.argv) > 1 and _sys.argv[1] in ("--version", "-v"):
        print(f"CFMesh-AutoGUI v{__version__}")
        _sys.exit(0)

    logger.info("Starting CFMesh-AutoGUI v%s...", __version__)
    logger.info("Python %s, PySide6, PyVista, GMSH, meshio")
    logger.info("Args: %s", _sys.argv[1:])

    # Global exception hooks: catch unhandled exceptions and show them
    # in a dialog instead of crashing silently.
    _orig_excepthook = _sys.excepthook
    def _exception_hook(typ, val, tb):
        import traceback as _tb
        msg = "".join(_tb.format_exception(typ, val, tb))
        logging.getLogger(__name__).critical("Unhandled exception:\n%s", msg)
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "Fatal Error",
                f"Unhandled exception:\n\n{typ.__name__}: {val}\n\n"
                "Check the log for details.")
        except Exception:
            pass
        _orig_excepthook(typ, val, tb)
    _sys.excepthook = _exception_hook
    if hasattr(_sys, "unraisablehook"):
        _orig_unraisable = _sys.unraisablehook
        def _unraisable_hook(args):
            logging.getLogger(__name__).error(
                "Unraisable exception: %s", args.exc_value)
            _orig_unraisable(args)
        _sys.unraisablehook = _unraisable_hook

    app = QApplication(_sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("CFMesh-AutoGUI")
    app.setOrganizationDomain("cfmesh-autogui.local")
    _init_i18n(app)

    apply_app_icon(app)

    s = QSettings("cfmesh-autogui", "CFMesh-AutoGUI")
    saved_mode = s.value("ui/theme_mode", "system")
    set_theme_mode(saved_mode)

    splash_pix = make_splash_pixmap(dark=current_mode() == "dark")
    splash = QSplashScreen(splash_pix, Qt.WindowStaysOnTopHint)
    splash.setWindowIcon(make_app_icon())
    splash.show()
    app.processEvents()

    apply_theme(app)
    logging.getLogger(__name__).info(
        "Theme: requested=%s resolved=%s", current_request(), current_mode()
    )

    # Deferred until after the splash is visible — see the comment above the
    # other imports for why.
    from cfmesh_autogui.gui.main_window import MainWindow

    window = MainWindow()
    window.show()
    splash.finish(window)
    _load_plugins(window)
    _sys.exit(app.exec())


if __name__ == "__main__":
    # Required on Windows for any frozen (PyInstaller) exe that has a
    # dependency using multiprocessing's "spawn" start method — without
    # this, a spawned worker just re-runs this same frozen entry point
    # from scratch instead of running as a worker, which for a GUI app
    # means launching a full second copy of the window. Confirmed live:
    # a bare, no-argument child of the exact same exe (no --feature-detect
    # or any other distinguishing arg) appeared ~12s after a real
    # STEP-file meshing run started and immediately crashed — some
    # dependency in that path (numpy/scipy/pyvista/gmsh all use
    # multiprocessing internally in various places) spawns a worker.
    # freeze_support() must run before anything else, even other imports
    # that might themselves trigger a spawn.
    import multiprocessing
    multiprocessing.freeze_support()
    main()
