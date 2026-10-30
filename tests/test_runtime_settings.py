from __future__ import annotations

import sys
import json

import pytest

import runtime_settings as runtime


def test_runtime_round_trip_without_dataset_and_rejected_write_is_atomic(tmp_path, monkeypatch):
    config = tmp_path / 'runtime.json'
    monkeypatch.setattr(runtime, 'runtime_settings_path', lambda: config)
    assert runtime.load_runtime_settings()['backend'] in {'cpu', 'auto', 'gpu'}
    runtime.save_runtime_settings({'workers': 3, 'backend': 'cpu'})
    original = config.read_bytes()
    assert runtime.load_runtime_settings() == {'workers': 3, 'backend': 'cpu'}
    for bad in ({'workers': 0}, {'workers': 33}, {'workers': True}, {'backend': 'invalid'}):
        with pytest.raises(ValueError):
            runtime.save_runtime_settings(bad)
        assert config.read_bytes() == original


def test_installer_rejects_unknown_specs_before_starting_process(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid input must not start an installer')
    monkeypatch.setattr(runtime, '_run_streamed', unexpected)
    with pytest.raises(ValueError):
        runtime.install_optional_packages(['https://example.com/package.whl'], lambda _: None)


def test_frozen_installer_targets_persistent_environment_not_exe(tmp_path, monkeypatch):
    interpreter = tmp_path / 'Scripts' / 'python.exe'
    target = tmp_path / 'Lib' / 'site-packages'
    commands = []
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(runtime, '_ensure_frozen_venv', lambda emit: interpreter)
    monkeypatch.setattr(runtime, 'runtime_environment_site_packages', lambda: target)
    monkeypatch.setattr(runtime, '_run_streamed', lambda cmd, emit: commands.append(cmd))
    runtime.install_optional_packages(['cvxopt'], lambda _: None)
    assert len(commands) == 1
    assert commands[0][:4] == [str(interpreter), '-m', 'pip', 'install']
    assert commands[0][commands[0].index('--target') + 1] == str(target)


def test_driver_wheel_selection_rejects_unsupported_cuda():
    assert runtime.compatible_cuda_wheels({'nvidia': {'cuda': '11.8'}}) == ()
    with pytest.raises(ValueError):
        runtime.selected_cupy_package({'nvidia': {'cuda': '12.6'}}, '13x')
    assert '12x' in runtime.selected_cupy_package({'nvidia': {'cuda': '12.6'}}, '12x')


def test_configuration_gui_round_trip_and_rejects_invalid_without_partial_application(tmp_path, monkeypatch):
    import tkinter as tk
    import app
    import settings_ui

    monkeypatch.setenv('PYTSA_HOME', str(tmp_path / 'workspace'))
    monkeypatch.setattr(runtime, 'runtime_settings_path', lambda: tmp_path / 'runtime.json')
    root = tk.Tk()
    errors = []
    monkeypatch.setattr(settings_ui.messagebox, 'showerror', lambda *args, **kwargs: errors.append(args))
    path = tmp_path / 'configuration.json'
    monkeypatch.setattr(settings_ui.filedialog, 'asksaveasfilename', lambda **kwargs: str(path))
    monkeypatch.setattr(settings_ui.filedialog, 'askopenfilename', lambda **kwargs: str(path))
    try:
        ui = app.PytsaApp(root)
        ui.fs_var.set('25')
        ui.additional_vars['prepare']['smoothing'].set('median')
        ui.settings_export_configuration()
        assert path.is_file(), errors
        ui.fs_var.set('10')
        ui.additional_vars['prepare']['smoothing'].set('none')
        ui.settings_import_configuration()
        assert not errors
        assert float(ui.fs_var.get()) == 25
        assert ui.additional_vars['prepare']['smoothing'].get() == 'median'
        valid = json.loads(path.read_text(encoding='utf-8'))
        valid['parameters']['prepare']['value_cols'] = [f'channel_{i}' for i in range(33)]
        ui._settings_validate_configuration(valid)
        valid['parameters']['prepare']['value_cols'] = []
        valid['parameters']['prepare']['fs'] = -1
        valid['parameters']['prepare']['smoothing'] = 'invalid'
        path.write_text(json.dumps(valid), encoding='utf-8')
        ui.settings_import_configuration()
        assert errors
        assert float(ui.fs_var.get()) == 25
        assert ui.additional_vars['prepare']['smoothing'].get() == 'median'
    finally:
        root.destroy()
