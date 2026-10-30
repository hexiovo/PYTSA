"""Windows regression: the startup root must never map with Tk's feather."""
import ctypes
import os
import tkinter as tk

import pytest

import app


@pytest.mark.skipif(os.name != "nt", reason="Native Windows window icons")
def test_startup_root_has_icons_before_main_ui_and_after_restore():
    app.set_windows_identity_and_dpi()
    root = tk.Tk()
    user32 = ctypes.WinDLL("user32")
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t

    def assert_native_icons():
        wrapper = user32.GetAncestor(root.winfo_id(), 2)
        assert all(user32.SendMessageW(wrapper, 0x007F, kind, 0)
                   for kind in (0, 1, 2))

    try:
        app._show_startup_splash(root)
        assert_native_icons()  # Before PytsaApp has configured its main window.
        root.iconify()
        root.update()
        root.deiconify()
        root.update()
        assert_native_icons()
    finally:
        root.destroy()
