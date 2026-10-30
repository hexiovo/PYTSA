from __future__ import annotations

import os
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk

import pytest

from app import PAGE_ORDER, PytsaApp, ScrollablePage


@pytest.fixture
def running_app():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk desktop is unavailable: {exc}")

    try:
        root.geometry("1080x720")
        app = PytsaApp(root)
        root.update()
        yield root, app
    finally:
        root.destroy()


def _nested_scroll_probe(page: ScrollablePage, label: str) -> tk.Label:
    frame = tk.Frame(page.inner)
    frame.pack(fill="x")
    probe = tk.Label(frame, text=label, height=80, anchor="w")
    probe.pack(fill="x")
    return probe


def _wheel(widget: tk.Misc, delta: int) -> None:
    widget.event_generate("<MouseWheel>", delta=delta, when="now")


def test_wheel_from_nested_descendants_scrolls_each_visible_page(running_app):
    root, app = running_app

    for page_id in PAGE_ORDER:
        app.show_page(page_id)
        page = app.pages[page_id]
        probe = _nested_scroll_probe(page, f"Wheel routing probe: {page_id}")
        root.update()
        before = page.canvas.yview()

        _wheel(probe, -120)
        root.update()

        after = page.canvas.yview()
        assert after[0] > before[0], f"MouseWheel from a nested child did not scroll {page_id}"


def test_wheel_preserves_native_widgets_hidden_pages_and_fractional_direction(running_app):
    root, app = running_app
    app.show_page("input")
    page = app.pages["input"]
    probe = _nested_scroll_probe(page, "Wheel routing boundary probe")

    text = tk.Text(page.inner, height=5, width=36)
    text.insert("1.0", "text row\n" * 80)
    text.pack(fill="x")

    listbox = tk.Listbox(page.inner, height=5)
    for index in range(80):
        listbox.insert("end", f"list row {index}")
    listbox.pack(fill="x")

    tree = ttk.Treeview(page.inner, columns=("value",), show="headings", height=5)
    tree.heading("value", text="Tree row")
    for index in range(80):
        tree.insert("", "end", values=(f"tree row {index}",))
    tree.pack(fill="x")

    selection = tk.StringVar(master=root, value="B")
    combo = app._combo(page.inner, selection, ["A", "B", "C"], readonly=True)
    combo.pack(fill="x")

    nested_canvas = tk.Canvas(page.inner, width=400, height=80)
    nested_canvas.pack(fill="x")

    root.update()
    page.canvas.yview_moveto(0)

    for native_widget in (text, listbox, tree):
        native_widget.yview_moveto(0)
        root.update()
        page_before = page.canvas.yview()
        native_before = native_widget.yview()

        _wheel(native_widget, -120)
        root.update()

        assert page.canvas.yview() == pytest.approx(page_before)
        assert native_widget.yview()[0] > native_before[0]

    page_before = page.canvas.yview()
    selected_index = combo.current()
    _wheel(combo, -120)
    root.update()
    assert combo.current() == selected_index
    assert page.canvas.yview() == pytest.approx(page_before)

    page_before = page.canvas.yview()
    _wheel(nested_canvas, -120)
    root.update()
    assert page.canvas.yview() == pytest.approx(page_before)

    popup = tk.Toplevel(root)
    popup_listbox = tk.Listbox(popup, height=4)
    for index in range(80):
        popup_listbox.insert("end", f"popup row {index}")
    popup_listbox.pack()
    root.update()
    popup_before = popup_listbox.yview()
    page_before = page.canvas.yview()
    _wheel(popup_listbox, -120)
    root.update()
    assert popup_listbox.yview()[0] > popup_before[0]
    assert page.canvas.yview() == pytest.approx(page_before)

    hidden_page = app.pages["workflow"]
    hidden_probe = _nested_scroll_probe(hidden_page, "Hidden page probe")
    app.show_page("input")
    root.update()
    hidden_before = hidden_page.canvas.yview()
    app._route_mousewheel(SimpleNamespace(widget=hidden_probe, delta=-120, num=None))
    assert hidden_page.canvas.yview() == pytest.approx(hidden_before)

    page.canvas.yview_moveto(0)
    root.update()
    before = page.canvas.yview()
    probe.event_generate("<Button-5>", when="now")
    root.update()
    assert page.canvas.yview()[0] > before[0]

    if os.name == "nt":
        page.canvas.yview_moveto(0)
        root.update()
        before = page.canvas.yview()
        _wheel(probe, -60)
        root.update()
        assert page.canvas.yview() == pytest.approx(before)

        _wheel(probe, -60)
        root.update()
        moved_down = page.canvas.yview()
        assert moved_down[0] > before[0]

        _wheel(probe, 60)
        root.update()
        assert page.canvas.yview() == pytest.approx(moved_down)
        _wheel(probe, 60)
        root.update()
        assert page.canvas.yview()[0] < moved_down[0]
