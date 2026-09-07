# ✅ F-023: __all__ esplicito per esportare API pubbliche
from polyfoammesh.gui.main_window import MainWindow
from polyfoammesh.gui.params_panel import ParamsPanel
from polyfoammesh.gui.quality_panel import QualityPanel
from polyfoammesh.gui.viewer_widget import ViewerWidget
from polyfoammesh.gui.log_panel import LogPanel
from polyfoammesh.gui.about_dialog import AboutDialog
from polyfoammesh.gui.new_case_wizard import NewCaseWizard

__all__ = [
    "MainWindow",
    "ParamsPanel",
    "QualityPanel",
    "ViewerWidget",
    "LogPanel",
    "AboutDialog",
    "NewCaseWizard",
]
