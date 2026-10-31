"""The Explorer right-click entry '720p for Plex (Nous Reel)'. Per-user only
(HKEY_CURRENT_USER), so no admin rights are needed and removing it leaves
nothing behind."""
import os
import sys
import winreg

VIDEO_EXTS = [".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".webm", ".mpg", ".mpeg",
              ".vob", ".flv"]
VERB = "NousReel.Plex720"
LABEL = "720p for Plex (Nous Reel)"
# tests point this somewhere harmless
BASE = os.environ.get("NOUSREEL_SHELL_BASE", r"Software\Classes\SystemFileAssociations")
HERE = os.path.dirname(os.path.abspath(__file__))


def command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --plex720 "%1"'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.join(HERE, "nousreel.py")}" --plex720 "%1"'


def icon_path():
    """A .ico for the menu entry, written once to %LOCALAPPDATA%\\NousReel."""
    bundled = os.path.join(getattr(sys, "_MEIPASS", HERE), "icon.ico")
    if os.path.exists(bundled):
        return bundled
    folder = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "NousReel")
    path = os.path.join(folder, "icon.ico")
    if not os.path.exists(path):
        from nousreel import app_icon
        os.makedirs(folder, exist_ok=True)
        app_icon().pixmap(32, 32).save(path, "ICO")
    return path


def _key(ext, base=BASE):
    return f"{base}\\{ext}\\shell\\{VERB}"


def is_installed(base=BASE):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _key(".mkv", base)):
            return True
    except OSError:
        return False


def install(base=BASE, icon=None):
    cmd = command()
    icon = icon or icon_path()
    for ext in VIDEO_EXTS:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _key(ext, base)) as k:
            winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, LABEL)
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
            # one process per selected file; reel_quick funnels them into one queue
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _key(ext, base) + r"\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    _notify()


def uninstall(base=BASE):
    for ext in VIDEO_EXTS:
        for sub in (r"\command", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _key(ext, base) + sub)
            except OSError:
                pass
    _notify()


def _notify():
    """Tell Explorer file associations changed so the menu updates right away."""
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except (OSError, AttributeError):
        pass
