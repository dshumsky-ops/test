#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
import json
import re

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QColor, QPalette, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)


SCRIPT_PATH = Path(__file__).resolve().parent / "check_card.sh"
WORKFLOW_SCRIPT_PATH = Path(__file__).resolve().parent / "workflow_actions.sh"
GH_AUTH_SCRIPT_PATH = Path(__file__).resolve().parent / "gh_auth.sh"
DEVICE_STATE_ROLE = int(Qt.UserRole) + 1
DEVICE_PATH_ROLE = int(Qt.UserRole) + 2
DEVICE_PROGRESS_ROLE = int(Qt.UserRole) + 3


class DeviceListDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        state = index.data(DEVICE_STATE_ROLE)
        progress_value = index.data(DEVICE_PROGRESS_ROLE)
        progress = progress_value if isinstance(progress_value, int) and progress_value >= 0 else None
        color = None
        if state == "running":
            color = QColor("#8a6d00")
        elif state == "done":
            color = QColor("#1f7a1f")
        elif state == "failed":
            color = QColor("#a11a1a")

        opt = option
        bar_rect = None
        if progress is not None:
            bar_width = max(120, min(180, option.rect.width() // 3))
            bar_rect = option.rect.adjusted(option.rect.width() - bar_width - 10, 0, -10, 0)
            bar_rect.setTop(option.rect.center().y() - 6)
            bar_rect.setHeight(12)
            opt.rect = option.rect.adjusted(0, 0, -(bar_width + 18), 0)

        if color is not None:
            opt.palette.setColor(QPalette.Text, color)
            opt.palette.setColor(QPalette.HighlightedText, color)
            super().paint(painter, opt, index)
        else:
            super().paint(painter, opt, index)

        if bar_rect is None or progress is None:
            return

        track_color = QColor("#d8d8d8")
        border_color = QColor("#9a9a9a")
        fill_color = QColor("#c49a00")
        if state == "done":
            fill_color = QColor("#2a8a2a")
        elif state == "failed":
            fill_color = QColor("#b63a3a")

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(border_color)
        painter.setBrush(track_color)
        painter.drawRoundedRect(bar_rect, 3, 3)

        inner = bar_rect.adjusted(1, 1, -1, -1)
        fill_w = int(inner.width() * max(0, min(progress, 100)) / 100)
        if fill_w > 0:
            fill_rect = inner.adjusted(0, 0, -(inner.width() - fill_w), 0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill_color)
            painter.drawRoundedRect(fill_rect, 2, 2)
        painter.restore()


class DeviceListWidget(QListWidget):
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        item = self.itemAt(pos)
        if item is None:
            self.clearSelection()
            self.setCurrentItem(None)
        super().mousePressEvent(event)


def _parse_yaml_scalar(value: str):
    value = value.strip()
    if not value:
        return ""
    if value in {"true", "false", "null"}:
        return {"true": True, "false": False, "null": None}[value]
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    return value


def parse_workflow_options_yaml(text: str) -> dict[str, dict[str, object]]:
    inputs: dict[str, dict[str, object]] = {}
    lines = text.splitlines()
    in_inputs = False
    current_input: str | None = None
    current_key: str | None = None

    for raw_line in lines:
        if not raw_line.strip():
            continue

        if not in_inputs:
            if raw_line.strip() == "inputs:":
                in_inputs = True
            continue

        if raw_line.startswith("  ") and not raw_line.startswith("    "):
            line = raw_line.strip()
            if line == "{}":
                break
            if line.endswith(":"):
                current_input = line[:-1]
                inputs[current_input] = {}
                current_key = None
            continue

        if current_input is None:
            continue

        if raw_line.startswith("    "):
            stripped = raw_line.strip()
            if stripped.endswith(":") and ":" not in stripped[:-1]:
                current_key = stripped[:-1]
                if current_key == "options":
                    inputs[current_input][current_key] = []
                continue

            if ":" in stripped:
                key, value = stripped.split(":", 1)
                inputs[current_input][key.strip()] = _parse_yaml_scalar(value)
                current_key = None
                continue

        if raw_line.startswith("      - ") and current_key == "options":
            item = raw_line.strip()[2:].strip()
            options = inputs[current_input].setdefault("options", [])
            if isinstance(options, list):
                options.append(_parse_yaml_scalar(item))

    return inputs


class WorkflowOptionsDialog(QDialog):
    start_requested = Signal(str, dict)

    def __init__(self, device_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.device_path = device_path
        self.setWindowTitle(f"Опції workflow: {device_path}")
        self.resize(760, 520)

        self.status_label = QLabel("Завантаження опцій workflow...")
        self.form_host = QWidget()
        self.form_layout = QFormLayout(self.form_host)
        self.form_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.form_layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.form_host)

        self.run_button = QPushButton("Запустити workflow")
        self.run_button.clicked.connect(self.run_workflow)

        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)
        layout.addWidget(self.scroll_area)

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.SeparateChannels)
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_error)

        self._last_error_text: str | None = None
        self._last_options_payload: str | None = None
        self._field_widgets: dict[str, QWidget] = {}
        self.load_options()

    def load_options(self) -> None:
        if not WORKFLOW_SCRIPT_PATH.exists():
            self.status_label.setText("Не знайдено workflow_actions.sh")
            self._clear_form()
            return

        if self.process.state() != QProcess.NotRunning:
            return

        self.run_button.setEnabled(False)
        self.status_label.setText("Завантаження опцій workflow...")

        env = QProcessEnvironment.systemEnvironment()
        path = env.value("PATH") or os.environ.get("PATH", "")
        if path:
            env.insert("PATH", path)
        self.process.setProcessEnvironment(env)

        self.process.start("/bin/bash", [str(WORKFLOW_SCRIPT_PATH), "--show-options"])

    def _on_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self.run_button.setEnabled(True)

        stdout = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        stderr = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace").strip()

        if exit_code != 0:
            self.status_label.setText(f"Помилка завантаження (код {exit_code})")
            self._clear_form()
            if stderr and stderr != self._last_error_text:
                self._last_error_text = stderr
                QMessageBox.critical(self, "Помилка workflow", stderr)
            return

        self._last_error_text = None
        payload = stdout.rstrip()
        if payload != self._last_options_payload:
            self._last_options_payload = payload
            self._rebuild_form_from_yaml(payload)
        self.status_label.setText("Опції workflow завантажено")

    def _on_error(self, error: QProcess.ProcessError) -> None:
        self.status_label.setText("Помилка запуску workflow_actions.sh")
        err_text = f"Не вдалося запустити workflow_actions.sh ({error})"
        if err_text != self._last_error_text:
            self._last_error_text = err_text
            QMessageBox.critical(self, "Помилка запуску", err_text)

    def run_workflow(self) -> None:
        self.start_requested.emit(self.device_path, self._collect_form_values())
        self.accept()

    def _collect_form_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for key, widget in self._field_widgets.items():
            if isinstance(widget, QComboBox):
                values[key] = widget.currentText().strip()
            elif isinstance(widget, QLineEdit):
                values[key] = widget.text().strip()
        return values

    def _clear_form(self) -> None:
        while self.form_layout.rowCount() > 0:
            self.form_layout.removeRow(0)
        self._field_widgets.clear()

    def _rebuild_form_from_yaml(self, yaml_text: str) -> None:
        self._clear_form()
        try:
            inputs = parse_workflow_options_yaml(yaml_text)
        except Exception as exc:
            self.status_label.setText("Помилка парсингу опцій")
            QMessageBox.critical(self, "Помилка парсингу", str(exc))
            return

        if not inputs:
            self.form_layout.addRow(QLabel("Немає доступних inputs"))
            return

        for input_name, meta in inputs.items():
            widget = self._build_input_widget(input_name, meta)
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            label_text = input_name
            if bool(meta.get("required")):
                label_text += " *"
            label = QLabel(label_text)
            label.setToolTip(str(meta.get("description", "")))
            self.form_layout.addRow(label, widget)
            self._field_widgets[input_name] = widget

    def _build_input_widget(self, input_name: str, meta: dict[str, object]) -> QWidget:
        options = meta.get("options")
        default_value = meta.get("default")
        input_type = str(meta.get("type", "") or "")

        if isinstance(options, list) and options:
            combo = QComboBox()
            values = ["" if v is None else str(v) for v in options]
            combo.addItems(values)
            if default_value is not None:
                idx = combo.findText(str(default_value))
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            return combo

        if input_type.lower() == "boolean":
            combo = QComboBox()
            combo.addItems(["false", "true"])
            if isinstance(default_value, bool):
                combo.setCurrentText("true" if default_value else "false")
            elif default_value is not None:
                combo.setCurrentText(str(default_value).lower())
            return combo

        line_edit = QLineEdit()
        if default_value is not None:
            line_edit.setText(str(default_value))

        description = str(meta.get("description", "") or "")
        if description:
            line_edit.setPlaceholderText(description)
            line_edit.setToolTip(description)
        else:
            line_edit.setPlaceholderText(input_name)
        return line_edit

@dataclass
class WorkflowRunInfo:
    device_path: str
    state: str = "running"
    run_url: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None


class WorkflowRunTask(QObject):
    state_changed = Signal(str, str)
    output_changed = Signal(str)
    finished_for_device = Signal(str)
    progress_changed = Signal(str, int)

    def __init__(self, device_path: str, inputs: dict[str, str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.info = WorkflowRunInfo(device_path=device_path)
        self._inputs = inputs
        self._pending_workflow_args: list[str] | None = None
        self.auth_process = QProcess(self)
        self._flash_total_bytes: int | None = None
        self._last_progress_percent = -1

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_error)

    def start(self) -> None:
        if not WORKFLOW_SCRIPT_PATH.exists():
            self.info.stderr += f"Workflow script not found: {WORKFLOW_SCRIPT_PATH}\n"
            self.info.state = "failed"
            self.state_changed.emit(self.info.device_path, "failed")
            self.output_changed.emit(self.info.device_path)
            self.finished_for_device.emit(self.info.device_path)
            return

        args = [str(WORKFLOW_SCRIPT_PATH), "--run-custom"]
        args.extend(["--device-path", self.info.device_path])
        for key, value in self._inputs.items():
            if value == "":
                continue
            args.extend(["--input", f"{key}={value}"])
        self._pending_workflow_args = args

        env = QProcessEnvironment.systemEnvironment()
        path = env.value("PATH") or os.environ.get("PATH", "")
        if path:
            env.insert("PATH", path)
        self.process.setProcessEnvironment(env)
        self.state_changed.emit(self.info.device_path, "running")
        self._start_workflow_process()

    def _start_workflow_process(self) -> None:
        if self._pending_workflow_args is None:
            return
        self.process.start("/bin/bash", self._pending_workflow_args)

    def _read_stdout(self) -> None:
        chunk = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not chunk:
            return
        self.info.stdout += chunk
        for line in chunk.splitlines():
            line = line.strip()
            if line.startswith("https://github.com/") and "/actions/runs/" in line:
                self.info.run_url = line
        self._update_flash_progress()
        self.output_changed.emit(self.info.device_path)

    def _read_stderr(self) -> None:
        chunk = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        if not chunk:
            return
        self.info.stderr += chunk
        self._update_flash_progress()
        self.output_changed.emit(self.info.device_path)

    def _update_flash_progress(self) -> None:
        combined = f"{self.info.stdout}\n{self.info.stderr}"
        if self._flash_total_bytes is None:
            size_match = re.search(r"FLASH_IMAGE_SIZE_BYTES=(\d+)", combined)
            if size_match:
                self._flash_total_bytes = int(size_match.group(1))
                if self._last_progress_percent < 0:
                    self._last_progress_percent = 0
                    self.progress_changed.emit(self.info.device_path, 0)

        if not self._flash_total_bytes:
            return

        dd_matches = re.findall(r"(?:^|[\r\n])\s*(\d+)\s+bytes\b[^\r\n]*\btransferred\b", combined)
        if not dd_matches:
            return

        transferred = int(dd_matches[-1])
        percent = max(0, min(100, int(transferred * 100 / self._flash_total_bytes)))
        if percent != self._last_progress_percent:
            self._last_progress_percent = percent
            self.progress_changed.emit(self.info.device_path, percent)

    def _on_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._read_stdout()
        self._read_stderr()
        self.info.exit_code = exit_code
        self.info.state = "done" if exit_code == 0 else "failed"
        if exit_code == 0 and self._last_progress_percent != 100:
            self._last_progress_percent = 100
            self.progress_changed.emit(self.info.device_path, 100)
        self.state_changed.emit(self.info.device_path, self.info.state)
        self.finished_for_device.emit(self.info.device_path)
        self.output_changed.emit(self.info.device_path)

    def _on_error(self, _error: QProcess.ProcessError) -> None:
        self._read_stdout()
        self._read_stderr()
        self.info.state = "failed"
        self.state_changed.emit(self.info.device_path, "failed")
        self.output_changed.emit(self.info.device_path)


class WorkflowStatusDialog(QDialog):
    def __init__(self, device_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.device_path = device_path
        self.setWindowTitle(f"Статус workflow: {device_path}")
        self.resize(780, 520)

        self.status_label = QLabel("Немає даних")
        self.run_url_label = QLabel("")
        self.run_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.output_view = QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setLineWrapMode(QPlainTextEdit.NoWrap)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addWidget(self.run_url_label)
        layout.addWidget(self.output_view)

    def set_info(self, info: WorkflowRunInfo) -> None:
        state_map = {
            "running": "Workflow виконується",
            "done": "Workflow завершено успішно",
            "failed": "Workflow завершився з помилкою",
        }
        self.status_label.setText(state_map.get(info.state, info.state))
        self.run_url_label.setText(info.run_url)
        text = ""
        if info.stdout:
            text += "[stdout]\n" + info.stdout.rstrip() + "\n"
        if info.stderr:
            if text:
                text += "\n"
            text += "[stderr]\n" + info.stderr.rstrip()
        self.output_view.setPlainText(text.rstrip())
        cursor = self.output_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.output_view.setTextCursor(cursor)


class AuthOutputDialog(QDialog):
    def __init__(self, device_path: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"gh_auth.sh output: {device_path}")
        self.resize(760, 420)

        label = QLabel("Вывод gh_auth.sh")
        self.output_view = QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output_view.setPlainText(text)

        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addWidget(self.output_view)

    def set_text(self, text: str) -> None:
        self.output_view.setPlainText(text)
        cursor = self.output_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.output_view.setTextCursor(cursor)


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Зборщик-прошиватор 9000")
        self.resize(520, 360)

        self.status_label = QLabel("Готово")
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._last_status_text = "Готово"
        self._last_devices: list[str] | None = None
        self._last_device_rows: list[tuple[str, str, str]] | None = None
        self._last_error: tuple[int, str] | None = None
        self._device_states: dict[str, str] = {}
        self._device_progress: dict[str, int] = {}
        self._workflow_runs: dict[str, WorkflowRunTask] = {}
        self._workflow_history: dict[str, WorkflowRunInfo] = {}
        self._startup_auth_stdout = ""
        self._startup_auth_stderr = ""
        self._startup_auth_last_emitted_text = ""
        self._startup_auth_browser_opened = False
        self._startup_auth_enter_sent = False

        self.list_widget = DeviceListWidget()
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.list_widget.setItemDelegate(DeviceListDelegate(self.list_widget))
        self.list_widget.itemDoubleClicked.connect(self._open_workflow_options)

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.SeparateChannels)
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_error)

        self.startup_auth_process = QProcess(self)
        self.startup_auth_process.setProcessChannelMode(QProcess.SeparateChannels)
        self.startup_auth_process.readyReadStandardOutput.connect(self._read_startup_auth_stdout)
        self.startup_auth_process.readyReadStandardError.connect(self._read_startup_auth_stderr)
        self.startup_auth_process.finished.connect(self._on_startup_auth_finished)
        self.startup_auth_process.errorOccurred.connect(self._on_startup_auth_error)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_devices)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.status_label)

        self.refresh_devices()
        self.timer.start()

        self._dialogs: list[WorkflowOptionsDialog] = []
        self._status_dialogs: dict[str, WorkflowStatusDialog] = {}
        self._auth_output_dialogs: dict[str, AuthOutputDialog] = {}
        QTimer.singleShot(0, self._start_startup_auth)

    def _start_startup_auth(self) -> None:
        if not GH_AUTH_SCRIPT_PATH.exists():
            return
        if self.startup_auth_process.state() != QProcess.NotRunning:
            return
        env = QProcessEnvironment.systemEnvironment()
        path = env.value("PATH") or os.environ.get("PATH", "")
        if path:
            env.insert("PATH", path)
        self.startup_auth_process.setProcessEnvironment(env)
        self.startup_auth_process.start("/bin/bash", [str(GH_AUTH_SCRIPT_PATH)])

    def _read_startup_auth_stdout(self) -> None:
        chunk = bytes(self.startup_auth_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not chunk:
            return
        self._startup_auth_stdout += chunk
        self._handle_startup_auth_interactive_output(chunk)
        self._emit_startup_auth_output_update()

    def _read_startup_auth_stderr(self) -> None:
        chunk = bytes(self.startup_auth_process.readAllStandardError()).decode("utf-8", errors="replace")
        if not chunk:
            return
        self._startup_auth_stderr += chunk
        self._handle_startup_auth_interactive_output(chunk)
        self._emit_startup_auth_output_update()

    def _handle_startup_auth_interactive_output(self, chunk: str) -> None:
        url_match = re.search(r"(https://github\.com/login/device)\b", chunk)
        if url_match and not self._startup_auth_browser_opened:
            self._startup_auth_browser_opened = True
            self._open_auth_browser("startup", url_match.group(1))
        if "Press Enter to open https://github.com/login/device" in chunk and not self._startup_auth_enter_sent:
            self._startup_auth_enter_sent = True
            self.startup_auth_process.write(b"\n")

    def _emit_startup_auth_output_update(self) -> None:
        text = ""
        if self._startup_auth_stdout.strip():
            text += "[stdout]\n" + self._startup_auth_stdout.rstrip() + "\n"
        if self._startup_auth_stderr.strip():
            if text:
                text += "\n"
            text += "[stderr]\n" + self._startup_auth_stderr.rstrip()
        text = text.rstrip()
        if text and text != self._startup_auth_last_emitted_text:
            self._startup_auth_last_emitted_text = text
            self._show_auth_output_dialog("gh_auth.sh", text)

    def _on_startup_auth_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._read_startup_auth_stdout()
        self._read_startup_auth_stderr()
        self._emit_startup_auth_output_update()
        if exit_code == 0:
            auth_dialog = self._auth_output_dialogs.pop("gh_auth.sh", None)
            if auth_dialog is not None:
                auth_dialog.close()
            return
        if exit_code != 0:
            err_text = "gh_auth.sh завершився з помилкою"
            if self._startup_auth_stderr.strip():
                err_text += f"\n\n{self._startup_auth_stderr.strip()}"
            QMessageBox.warning(self, "GitHub авторизація", err_text)

    def _on_startup_auth_error(self, error: QProcess.ProcessError) -> None:
        self._read_startup_auth_stdout()
        self._read_startup_auth_stderr()
        self._emit_startup_auth_output_update()
        QMessageBox.warning(self, "GitHub авторизація", f"Не вдалося запустити gh_auth.sh ({error})")

    def _set_status(self, text: str) -> None:
        if text == self._last_status_text:
            return
        self._last_status_text = text
        self.status_label.setText(text)

    def refresh_devices(self) -> None:
        if not SCRIPT_PATH.exists():
            self._set_status("Не знайдено check_card.sh")
            return

        if self.process.state() != QProcess.NotRunning:
            return

        env = QProcessEnvironment.systemEnvironment()
        path = env.value("PATH") or os.environ.get("PATH", "")
        if path:
            env.insert("PATH", path)
        self.process.setProcessEnvironment(env)

        self.process.start("/bin/bash", [str(SCRIPT_PATH)])

    def _on_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        stdout = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        stderr = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace").strip()

        if exit_code != 0:
            self._set_status(f"Помилка (код {exit_code})")
            if stderr and self._last_error != (exit_code, stderr):
                self._last_error = (exit_code, stderr)
                QMessageBox.critical(self, "Помилка виконання", stderr)
            return

        self._last_error = None
        device_rows: list[tuple[str, str, str]] = []
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split("\t")]
            dev = parts[0] if parts else ""
            if not dev:
                continue
            size = parts[1] if len(parts) > 1 else ""
            manufacturer = parts[2] if len(parts) > 2 else ""
            device_rows.append((dev, size, manufacturer))

        if device_rows == (self._last_device_rows or []):
            return

        devices = [dev for dev, _, _ in device_rows]

        previous_devices = set(self._last_devices or [])
        current_devices = set(devices)
        removed_devices = previous_devices - current_devices

        previous_rows_map = {dev: (dev, size, manufacturer) for dev, size, manufacturer in (self._last_device_rows or [])}
        retained_missing_devices: list[str] = []
        for dev in sorted(removed_devices):
            task = self._workflow_runs.get(dev)
            if task is not None and (
                task.auth_process.state() != QProcess.NotRunning
                or task.process.state() != QProcess.NotRunning
            ):
                retained_missing_devices.append(dev)

        if retained_missing_devices:
            retained_set = set(retained_missing_devices)
            removed_devices = removed_devices - retained_set
            for dev in retained_missing_devices:
                row = previous_rows_map.get(dev, (dev, "", ""))
                if row[0] not in current_devices:
                    device_rows.append(row)
                    devices.append(row[0])
                    current_devices.add(row[0])

        if removed_devices:
            self._forget_removed_devices(removed_devices)

        self._last_devices = devices
        self._last_device_rows = device_rows
        self.list_widget.clear()

        if not devices:
            self._set_status("USB-пристроїв не знайдено")
            return

        for dev, size, manufacturer in device_rows:
            details = " | ".join(part for part in (size, manufacturer) if part)
            label = f"{dev} ({details})" if details else dev
            item = QListWidgetItem(label, self.list_widget)
            item.setData(DEVICE_PATH_ROLE, dev)
            self._apply_device_item_style(item, self._device_states.get(dev))

        self._set_status(f"Знайдено пристроїв: {len(devices)}")

    def _on_error(self, error: QProcess.ProcessError) -> None:
        self._set_status("Помилка запуску процесу")
        err_text = f"Не вдалося запустити check_card.sh ({error})"
        if self._last_error != (-1, err_text):
            self._last_error = (-1, err_text)
            QMessageBox.critical(self, "Помилка запуску", err_text)

    def _open_workflow_options(self, item: QListWidgetItem) -> None:
        device_path = str(item.data(DEVICE_PATH_ROLE) or item.text()).strip()
        if not device_path:
            return

        if device_path in self._workflow_history:
            self._open_workflow_status(device_path)
            return

        dialog = WorkflowOptionsDialog(device_path, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.start_requested.connect(self._start_workflow_for_device)
        dialog.destroyed.connect(lambda *_: self._dialogs.remove(dialog) if dialog in self._dialogs else None)
        self._dialogs.append(dialog)
        dialog.show()

    def _on_workflow_state_changed(self, device_path: str, state: str) -> None:
        self._device_states[device_path] = state
        if state == "done":
            self._device_progress[device_path] = 100
        self._update_device_item_style(device_path)

    def _on_workflow_progress_changed(self, device_path: str, percent: int) -> None:
        self._device_progress[device_path] = max(0, min(100, percent))
        self._update_device_item_style(device_path)

    def _start_workflow_for_device(self, device_path: str, inputs: dict) -> None:
        existing_task = self._workflow_runs.get(device_path)
        if existing_task is not None:
            if (
                existing_task.auth_process.state() != QProcess.NotRunning
                or existing_task.process.state() != QProcess.NotRunning
            ):
                self._open_workflow_status(device_path)
                return

        task = WorkflowRunTask(device_path, {str(k): str(v) for k, v in inputs.items()}, self)
        task.state_changed.connect(self._on_workflow_state_changed)
        task.progress_changed.connect(self._on_workflow_progress_changed)
        task.output_changed.connect(self._on_workflow_output_changed)
        task.finished_for_device.connect(self._on_workflow_task_finished)
        self._workflow_runs[device_path] = task
        self._workflow_history[device_path] = task.info
        task.start()
        self._open_workflow_status(device_path)

    def _on_workflow_output_changed(self, device_path: str) -> None:
        task = self._workflow_runs.get(device_path)
        if task is not None:
            self._workflow_history[device_path] = task.info
        dialog = self._status_dialogs.get(device_path)
        if dialog and device_path in self._workflow_history:
            dialog.set_info(self._workflow_history[device_path])

    def _on_workflow_task_finished(self, device_path: str) -> None:
        task = self._workflow_runs.get(device_path)
        if task is not None:
            self._workflow_history[device_path] = task.info

    def _show_auth_output_dialog(self, device_path: str, text: str) -> None:
        dialog = self._auth_output_dialogs.get(device_path)
        if dialog is None:
            dialog = AuthOutputDialog(device_path, text, self)
            dialog.setAttribute(Qt.WA_DeleteOnClose, True)
            dialog.destroyed.connect(lambda *_: self._auth_output_dialogs.pop(device_path, None))
            self._auth_output_dialogs[device_path] = dialog
        else:
            dialog.set_text(text)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_auth_browser(self, _device_path: str, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _open_workflow_status(self, device_path: str) -> None:
        dialog = self._status_dialogs.get(device_path)
        if dialog is None:
            dialog = WorkflowStatusDialog(device_path, self)
            dialog.setAttribute(Qt.WA_DeleteOnClose, True)
            dialog.destroyed.connect(lambda *_: self._status_dialogs.pop(device_path, None))
            self._status_dialogs[device_path] = dialog

        info = self._workflow_history.get(device_path)
        if info is not None:
            dialog.set_info(info)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forget_removed_devices(self, removed_devices: set[str]) -> None:
        for device_path in removed_devices:
            self._device_states.pop(device_path, None)
            self._device_progress.pop(device_path, None)
            self._workflow_history.pop(device_path, None)

            task = self._workflow_runs.pop(device_path, None)
            if task is not None:
                if task.auth_process.state() != QProcess.NotRunning:
                    task.auth_process.kill()
                if task.process.state() != QProcess.NotRunning:
                    task.process.kill()

            dialog = self._status_dialogs.pop(device_path, None)
            if dialog is not None:
                dialog.close()

            auth_dialog = self._auth_output_dialogs.pop(device_path, None)
            if auth_dialog is not None:
                auth_dialog.close()

    def _update_device_item_style(self, device_path: str) -> None:
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item and str(item.data(DEVICE_PATH_ROLE) or item.text()) == device_path:
                self._apply_device_item_style(item, self._device_states.get(device_path))
                return

    def _apply_device_item_style(self, item: QListWidgetItem, state: str | None) -> None:
        item.setData(DEVICE_STATE_ROLE, state)
        device_path = str(item.data(DEVICE_PATH_ROLE) or item.text())
        progress = self._device_progress.get(device_path)
        item.setData(DEVICE_PROGRESS_ROLE, progress if progress is not None else -1)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
