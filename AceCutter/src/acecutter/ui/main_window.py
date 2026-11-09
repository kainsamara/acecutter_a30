"""Главное окно: импорт, раскладка по полю, предпросмотр, СТАРТ, связь с GRBL."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
import threading
import time

from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ..domain.document import Workspace
from ..domain.geometry import Transform
from ..domain.layout import (
    apply, center_x_transform, fit_width_transform, polylines_bounds,
    rotate_about_transform, to_origin_transform,
)
from ..driver.streamer import GrblStreamer
from ..driver.transport import SerialTransport, Transport
from ..io.dxf_import import import_dxf
from ..io.svg_import import import_svg
from ..toolpath.gcode import CutParams, compile_gcode, order_polylines
from ..toolpath.overcut import apply_overcut
from ..toolpath.toolhead import SpindlePen
from .canvas import CanvasView

PUMP_MS = 50
POLL_MS = 300


class MainWindow(QMainWindow):
    _port_opened = Signal(str, object, str)  # port, transport | None, error | ""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AceCutter")
        self.resize(1280, 860)

        self._workspace = Workspace()
        self._streamer: GrblStreamer | None = None
        self._transport: Transport | None = None
        self._job_total = 0
        self._job_done = 0
        self._job_sent = False  # страховка от повторной отправки задания
        self._last_ok = time.monotonic()
        self._run_state = "idle"  # idle | running | paused
        self._mwidth: float | None = None   # $130 со станка
        self._mlength: float | None = None  # $131 со станка
        self._connected_port: str | None = None
        self._suppress_port_change = True
        self._grbl_verified = False  # осталось для статуса; проверок больше нет
        self._port_texts: dict[str, str] = {}
        self._opening_port: str | None = None
        self._port_opened.connect(self._on_port_opened)

        self.canvas = CanvasView()
        self.canvas.set_workspace(self._workspace)
        self.canvas.ctxAction.connect(self._on_ctx_action)
        self.canvas.sizeHint.connect(self._set_status)

        self._build_toolbar()
        self._build_side_panel()
        self._build_shortcuts()

        splitter = QSplitter()
        splitter.addWidget(self.canvas)
        splitter.addWidget(self._side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        self.setCentralWidget(splitter)

        self._pump_timer = QTimer(self, interval=PUMP_MS, timeout=self._pump)
        self._poll_timer = QTimer(self, interval=POLL_MS, timeout=self._poll)
        self._pump_timer.start()
        self._poll_timer.start()

        # старт-вид: поле по ширине окна, ноль у левого края
        QTimer.singleShot(0, self.canvas.fit_width)
        # автоподключение к самому правдоподобному COM-порту
        QTimer.singleShot(600, self._autoconnect)

        self._status_label = QLabel("Готов")
        self.statusBar().addWidget(self._status_label, 1)
        # прогрессбар в нижней строке, шириной с панель управления (блок «Пуск»)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(348)
        self.progress.setMaximumHeight(16)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)

    # -- тулбар -----------------------------------------------------------

    def _build_toolbar(self) -> None:
        pass  # весь орган управления — в правой панели, сверху ничего не висит

    def _build_shortcuts(self) -> None:
        # буквы работают по всему окну (WidgetWithChildren): спинбоксы буквы
        # всё равно не едят, а канвас не текстовое поле
        for seq, fn in (
            (QKeySequence(Qt.Key.Key_Delete), self._delete_selected),
            (QKeySequence.StandardKey.SelectAll, self.canvas.select_all),
            (QKeySequence(Qt.Key.Key_Escape), self._esc_pressed),
            (QKeySequence(Qt.Key.Key_Return), self._enter_pressed),
            (QKeySequence(Qt.Key.Key_Enter), self._enter_pressed),
            (QKeySequence(Qt.Key.Key_C), self._align_center_x),
            (QKeySequence(Qt.Key.Key_X), self._align_origin),
            (QKeySequence(Qt.Key.Key_V), self._align_fit),
            (QKeySequence(Qt.Key.Key_R), self._rotate_selected),
        ):
            act = QAction(self)
            act.setShortcut(seq)
            act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            act.triggered.connect(fn)
            self.addAction(act)

    def _esc_pressed(self) -> None:
        if self.canvas.editing:
            self.canvas.cancel_scale_edit()
        else:
            self.canvas.clear_selection()

    def _enter_pressed(self) -> None:
        if self.canvas.editing:
            self.canvas.apply_scale_edit()
        elif self.canvas.selected_count():
            self.canvas.enter_scale_edit()  # Enter по выделению = «Изменить»

    def _delete_selected(self) -> None:
        n = self.canvas.delete_selected()
        if n:
            left = len(self.canvas.polylines())
            self._set_status(f"Удалено контуров: {n} (осталось {left})")

    def _build_side_panel(self) -> None:
        self._side = QWidget()
        self._side.setFixedWidth(372)
        root = QVBoxLayout(self._side)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        content = QWidget()
        col = QVBoxLayout(content)
        col.setSpacing(8)

        # 1 · Станок: автоподключение, порт переключается прямо в строке,
        # индикатор GRBL живёт прямо в пунктах списка
        machine_box = QGroupBox("1 · Станок")
        m = QFormLayout(machine_box)
        m.setSpacing(6)
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(230)
        self._refresh_ports()
        self._port_combo.currentIndexChanged.connect(self._on_port_changed)
        m.addRow("Порт:", self._port_combo)
        col.addWidget(machine_box)

        # 2 · Файл
        file_box = QGroupBox("2 · Файл")
        f = QVBoxLayout(file_box)
        f.setSpacing(4)
        row_imp = QHBoxLayout()
        imp_svg = QPushButton("📥 Импорт SVG")
        imp_svg.setMinimumHeight(30)
        imp_svg.clicked.connect(lambda: self._import_file("svg"))
        imp_dxf = QPushButton("📥 Импорт DXF")
        imp_dxf.setMinimumHeight(30)
        imp_dxf.clicked.connect(lambda: self._import_file("dxf"))
        row_imp.addWidget(imp_svg)
        row_imp.addWidget(imp_dxf)
        f.addLayout(row_imp)
        export = QPushButton("💾 Экспорт GCode")
        export.setMinimumHeight(30)
        export.clicked.connect(self._export_gcode)
        f.addWidget(export)
        col.addWidget(file_box)

        # 3 · Управление: шаг, перо, $H/стрелки/PEN в один ряд
        ctrl_box = QGroupBox("3 · Управление")
        g = QFormLayout(ctrl_box)
        self._jog_step = QDoubleSpinBox()
        self._jog_step.setRange(0.1, 500.0)
        self._jog_step.setValue(10.0)
        self._jog_step.setSuffix(" мм")
        g.addRow("Шаг:", self._jog_step)
        self._pen_spin = QSpinBox()
        self._pen_spin.setRange(1, 1000)
        self._pen_spin.setValue(1000)
        g.addRow("Перо (M3 S):", self._pen_spin)
        row_ctrl = QHBoxLayout()
        home_btn = QPushButton("$H")
        home_btn.setFixedSize(44, 30)
        home_btn.clicked.connect(lambda: self._enqueue("$H"))
        row_ctrl.addWidget(home_btn)
        for text, dx, dy in (("←", -1, 0), ("↓", 0, -1), ("↑", 0, 1), ("→", 1, 0)):
            btn = QPushButton(text)
            btn.setFixedSize(44, 30)
            btn.clicked.connect(lambda *_, dx=dx, dy=dy: self._jog(dx, dy))
            row_ctrl.addWidget(btn)
        self._pen_btn = QPushButton("PEN")
        self._pen_btn.setFixedSize(56, 30)
        self._pen_btn.setCheckable(True)
        self._pen_btn.setToolTip("Опустить/поднять перо вручную")
        self._pen_btn.setStyleSheet(
            "QPushButton:checked { background: #d9962b; color: white; font-weight: bold; }")
        self._pen_btn.toggled.connect(self._toggle_pen)
        row_ctrl.addWidget(self._pen_btn)
        g.addRow(row_ctrl)
        col.addWidget(ctrl_box)

        # 4 · Инструмент и режим
        tool_box = QGroupBox("4 · Инструмент и режим")
        t = QFormLayout(tool_box)
        self._tool_combo = QComboBox()
        self._tool_combo.addItem("Карандаш", "pen")
        self._tool_combo.addItem("Резак", "cutter")
        t.addRow("Тип:", self._tool_combo)
        self._overcut_spin = QDoubleSpinBox()
        self._overcut_spin.setRange(0.0, 10.0)
        self._overcut_spin.setSingleStep(0.1)
        self._overcut_spin.setValue(0.5)
        self._overcut_spin.setSuffix(" мм")
        self._overcut_spin.setEnabled(False)
        self._tool_combo.currentIndexChanged.connect(
            lambda i: self._overcut_spin.setEnabled(i == 1))
        t.addRow("Overcut:", self._overcut_spin)
        self._feed_spin = QDoubleSpinBox()
        self._feed_spin.setRange(10, 30000)
        self._feed_spin.setValue(1500)
        self._feed_spin.setSuffix(" мм/мин")
        self._passes_spin = QSpinBox()
        self._passes_spin.setRange(1, 10)
        t.addRow("Подача:", self._feed_spin)
        t.addRow("Проходы:", self._passes_spin)
        col.addWidget(tool_box)

        col.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        root.addWidget(scroll, 1)

        # Пуск: главная кнопка-переключалка + Стоп, ниже — прогресс во всю ширину
        start_box = QGroupBox("Пуск")
        s = QVBoxLayout(start_box)
        s.setSpacing(6)
        run_row = QHBoxLayout()
        run_row.setSpacing(6)
        self._main_btn = QPushButton("  ⏺  СТАРТ  ")
        self._main_btn.setMinimumHeight(44)
        self._main_btn.clicked.connect(self._main_button)
        self._stop_btn = QPushButton("⏹ Стоп")
        self._stop_btn.setMinimumHeight(44)
        self._stop_btn.setMinimumWidth(96)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._soft_reset)
        run_row.addWidget(self._main_btn, 1)
        run_row.addWidget(self._stop_btn)
        s.addLayout(run_row)
        root.addWidget(start_box)
        self._update_run_controls()

    # -- поле/раскладка ------------------------------------------------------

    def _on_setting(self, num: int, value: float) -> None:
        """$$ со станка: $130/$131 — реальное рабочее поле (молча, без Labels)."""
        if num == 130:
            self._mwidth = value
        elif num == 131:
            self._mlength = value
        else:
            return
        if self._mwidth and self._mlength:
            self._workspace = Workspace(width_mm=self._mwidth,
                                        length_mm=self._mlength)
            self.canvas.set_workspace(self._workspace)

    def _align_center_x(self) -> None:
        if self.canvas.editing:
            return  # в правке контуры — управляемые копии, не подменяем
        sel = self.canvas.polylines_selected()
        if sel:
            self.canvas.replace_selected(apply(sel, center_x_transform(sel, self._workspace.width_mm)))

    def _align_origin(self) -> None:
        if self.canvas.editing:
            return
        sel = self.canvas.polylines_selected()
        if sel:
            self.canvas.replace_selected(apply(sel, to_origin_transform(sel)))

    def _align_fit(self) -> None:
        if self.canvas.editing:
            return
        sel = self.canvas.polylines_selected()
        if sel:
            self.canvas.replace_selected(apply(sel, fit_width_transform(sel, self._workspace.width_mm)))

    def _rotate_selected(self) -> None:
        """R: повернуть выделенное на 90° по часовой вокруг его центра."""
        if self.canvas.editing:
            return
        sel = self.canvas.polylines_selected()
        if sel:
            # по часовой на экране = -90° в математике поля (Y вверх)
            self.canvas.replace_selected(apply(sel, rotate_about_transform(sel, -90.0)))
            self._set_status("Повернуто на 90° по часовой")

    def _on_ctx_action(self, action: str) -> None:
        """Контекстное меню канваса: применяется только к выделенному."""
        if action == "resize":
            self.canvas.enter_scale_edit()
            return
        sel = self.canvas.polylines_selected()
        if not sel:
            return
        if action == "center":
            t = center_x_transform(sel, self._workspace.width_mm)
        elif action == "origin":
            t = to_origin_transform(sel)
        elif action == "fit":
            t = fit_width_transform(sel, self._workspace.width_mm)
        elif action == "rotate":
            t = rotate_about_transform(sel, -90.0)  # по часовой глазами юзера
        else:
            return
        self.canvas.replace_selected(apply(sel, t))

    # -- импорт/экспорт --------------------------------------------------------

    def _import_file(self, kind: str) -> None:
        if self._job_total and self._streamer and self._streamer.busy:
            QMessageBox.warning(self, "Идёт резка", "Дождись конца задания или жми Стоп.")
            return
        flt = ("SVG (*.svg);;Все файлы (*)" if kind == "svg"
               else "DXF (*.dxf);;Все файлы (*)")
        path, _ = QFileDialog.getOpenFileName(self, "Открыть файл", "", flt)
        if not path:
            return
        try:
            polylines = import_svg(path) if kind == "svg" else import_dxf(path)
        except Exception as e:  # noqa: BLE001 — показываем юзеру
            QMessageBox.critical(self, "Ошибка импорта", str(e))
            return
        if not polylines:
            QMessageBox.warning(self, "Пусто", "В файле не найдено векторных контуров.")
            return
        # рулон: шире поля не порежешь — вписываем; узкое не растягиваем
        polylines = apply(polylines, fit_width_transform(polylines, self._workspace.width_mm))
        polylines = apply(polylines, to_origin_transform(polylines))
        existing = self.canvas.polylines()
        b_old = polylines_bounds(existing)
        if b_old:
            # на поле уже есть детали — новый импорт кладём ВЫШЕ по рулону
            polylines = apply(polylines, Transform.translate(0.0, b_old[3] + 2.0))
            self.canvas.add_polylines(polylines)
        else:
            self.canvas.load_polylines(polylines)
        self.canvas.reveal_content()
        b = polylines_bounds(polylines)
        total = len(self.canvas.polylines())
        self._set_status(f"Импорт: +{len(polylines)} контуров, "
                         f"{b[2] - b[0]:.1f}×{b[3] - b[1]:.1f} мм (всего {total})")

    def _export_gcode(self) -> None:
        pls = self._job_polylines()
        if not pls:
            QMessageBox.information(self, "Пусто", "Сначала импортируй SVG/DXF.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить GCode", "", "GCode (*.nc *.gcode);;Все файлы (*)")
        if not path:
            return
        lines = compile_gcode(pls, self._params(), self._tool())
        with open(path, "w", encoding="ascii") as fh:
            fh.write("\n".join(lines) + "\n")
        self._set_status(f"GCode сохранён: {path} ({len(lines)} строк)")

    # -- инструмент ------------------------------------------------------------

    def _is_cutter(self) -> bool:
        return self._tool_combo.currentData() == "cutter"

    def _job_polylines(self) -> list[Polyline]:
        """Контуры с поля; в режиме резака — с продлением входа/выхода."""
        pls = self.canvas.polylines()
        if self._is_cutter():
            pls = apply_overcut(pls, self._overcut_spin.value())
        return pls

    # -- задание -----------------------------------------------------------------

    def _start_job(self) -> None:
        if self._run_state != "idle" or self._job_sent:
            return  # задание идёт/доезжает/уже отправлено — второго прогона не будет
        if self._streamer is None:
            QMessageBox.warning(self, "Нет связи", "Сначала подключись к плоттеру (или MOCK для теста).")
            return
        pls = self.canvas.polylines()
        if not pls:
            QMessageBox.information(self, "Пусто", "Нечего резать — импортируй файл.")
            return
        b = polylines_bounds(pls) or (0, 0, 0, 0)
        if b[0] < -0.5 or b[2] > self._workspace.width_mm + 0.5:
            answ = QMessageBox.question(
                self, "Шире поля",
                f"Деталь {b[0]:.1f}..{b[2]:.1f} мм выходит за ширину поля "
                f"{self._workspace.width_mm:.0f} мм.\nВсё равно резать?")
            if answ != QMessageBox.StandardButton.Yes:
                return
        lines = compile_gcode(self._job_polylines(), self._params(), self._tool())
        tool_note = (f", overcut {self._overcut_spin.value():.1f} мм"
                     if self._is_cutter() else "")
        # визуализация: тот же порядок контуров, что уйдёт в compile_gcode
        self.canvas.begin_job_overlay(order_polylines(self._job_polylines()))
        # старт сразу, без предварительного хоуминга (хом уходит при открытии порта)
        self._job_total = self._streamer.send_many(lines)  # один вызов!
        self._job_done = 0
        self.progress.setRange(0, self._job_total)
        self.progress.setValue(0)
        self.progress.show()
        self._job_sent = True
        self._set_pen_checked(False)  # пером теперь рулит программа
        self._set_run_state("running")
        self._set_status(f"СТАРТ: {self._job_total} строк{tool_note}")

    def _on_error_line(self, line: str, code: int) -> None:
        self._set_status(f"error:{code} на «{line}»")

    def _on_ok_line(self, line: str) -> None:
        self._last_ok = time.monotonic()
        if not self._job_total:
            return
        self._job_done += 1
        self.progress.setValue(min(self._job_done, self._job_total))
        if self._job_done >= self._job_total:
            # как в версии до правок масштаба: счётчик дошёл — сразу Готово
            self._job_total = 0
            self._job_sent = False
            self._set_run_state("idle")
            self.progress.setValue(self.progress.maximum())
            self.canvas.clear_job_overlay()  # всё снова синее
            self._set_status("Готово")
        else:
            self._set_status(f"Резка: {self._job_done}/{self._job_total}")

    # -- главная кнопка: СТАРТ → ПАУЗА → ПРОДОЛЖИТЬ, Стоп отдельно ---------

    _RUN_STYLE = {
        "idle": ("  ⏺  СТАРТ  ", "#2e9e4f", "#37b35c"),
        "running": ("  ⏸  ПАУЗА  ", "#d9962b", "#e8ab45"),
        "paused": ("  ▶  ПРОДОЛЖИТЬ  ", "#2e7d9e", "#3a96b8"),
    }

    def _set_run_state(self, state: str) -> None:
        self._run_state = state
        self._update_run_controls()

    def _update_run_controls(self) -> None:
        text, bg, hover = self._RUN_STYLE[self._run_state]
        self._main_btn.setText(text)
        self._main_btn.setStyleSheet(
            "QPushButton { background: %s; color: white; font-size: 15px;"
            " font-weight: bold; border-radius: 6px; }"
            "QPushButton:hover { background: %s; }" % (bg, hover))
        # Стоп мёртвая, пока ничего не делаем
        self._stop_btn.setEnabled(self._run_state != "idle")
        self._stop_btn.setStyleSheet(
            "QPushButton { background: %s; color: white; font-size: 13px;"
            " font-weight: bold; border-radius: 6px; }"
            "QPushButton:hover { background: #d64545; }"
            "QPushButton:disabled { background: #b8b8b8; color: #ececec; }"
            % ("#c0392b" if self._run_state != "idle" else "#b8b8b8"))

    def _main_button(self) -> None:
        if self._run_state == "idle":
            self._start_job()
        elif self._run_state == "running":
            self._realtime(b"!")   # feed hold
            self._set_run_state("paused")
        else:
            self._realtime(b"~")   # cycle start
            self._set_run_state("running")

    # -- станок: автоподключение, проверка GRBL, смена порта в строке ----------

    def _refresh_ports(self) -> None:
        self._suppress_port_change = True
        self._port_combo.clear()
        self._port_texts = {}
        try:
            from serial.tools.list_ports import comports
            for p in comports():
                # description уже содержит «(COMx)» — номер не дублируем
                text = p.description or p.device
                self._port_texts[p.device] = text
                self._port_combo.addItem(text, p.device)
        except Exception:
            pass
        self._suppress_port_change = False

    def _mark_port(self, port: str, suffix: str) -> None:
        """Индикатор состояния прямо в пункте списка портов."""
        base = self._port_texts.get(port, port)
        idx = self._port_combo.findData(port)
        if idx >= 0:
            self._port_combo.setItemText(idx, f"{base} {suffix}" if suffix else base)

    def _autoconnect(self) -> None:
        """При старте: выбрать самый правдоподобный порт (CH340/Arduino —
        типичные Nano/CNC Shield) и подключиться без лишних вопросов."""
        if self._connected_port is not None:
            return
        self._refresh_ports()
        candidates = self._candidate_ports()
        if not candidates:
            self._set_status("COM-порты не найдены — воткни плоттер")
            return
        port = candidates[0]
        idx = self._port_combo.findData(port)
        if idx >= 0:
            self._port_combo.setCurrentIndex(idx)
        self._connect_to(port)

    def _candidate_ports(self) -> list[str]:
        import re

        from serial.tools.list_ports import comports
        scored = []
        for p in comports():
            haystack = f"{p.description} {getattr(p, 'hwid', '')}".lower()
            score = 0
            if "ch340" in haystack or "ch341" in haystack:
                score = 3
            elif "arduino" in haystack:
                score = 2
            elif "usb serial" in haystack or "uart" in haystack:
                score = 1
            m = re.search(r"COM(\d+)", p.device)
            num = int(m.group(1)) if m else 9999
            scored.append((-score, num, p.device))
        return [dev for _, _, dev in sorted(scored)]

    def _on_port_changed(self, *_):
        """Юзер переключил порт в строке — мгновенно переподключаемся."""
        if getattr(self, "_suppress_port_change", False):
            return
        port = self._port_combo.currentData()
        if port and port != self._connected_port:
            self._connect_to(port)

    def _connect_to(self, port: str) -> None:
        """Открыть порт в фоновом потоке: некоторые COM-драйверы (bluetooth,
        модемы) открываются секундами и вешали UI."""
        self._close_connection()
        self._opening_port = port
        self._mark_port(port, "· подключаюсь…")
        self._set_status(f"Открываю {port}…")

        def worker():
            try:
                transport: object = SerialTransport(port, 115200)
                error = ""
            except Exception as e:  # noqa: BLE001
                transport, error = None, str(e)
            self._port_opened.emit(port, transport, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_port_opened(self, port: str, transport: object, error: str) -> None:
        if port != self._opening_port:
            if isinstance(transport, Transport):
                transport.close()  # юзер уже ушёл на другой порт
            return
        self._opening_port = None
        if transport is None:
            self._mark_port(port, f"· не открыть: {error}")
            self._set_status(f"{port}: не удалось открыть")
            return
        self._connected_port = port
        self._transport = transport  # type: ignore[assignment]
        self._mark_port(port, "· открыт")
        self._streamer = GrblStreamer(
            transport,  # type: ignore[arg-type]
            on_ok=self._on_ok_line,
            on_status=self._on_status,
            on_error=self._on_error_line,
            on_alarm=lambda code: self._set_status(f"ALARM:{code} — Стоп, потом $H"),
            on_setting=self._on_setting,
        )
        # GRBL после открытия порта (DTR-риджет Arduino) бутится 1–2 секунды —
        # немедленные команды теряются. Даём ему подняться, потом $$ и $H.
        streamer = self._streamer
        QTimer.singleShot(2000, lambda: self._grbl_hello(port, streamer))
        self._set_status(f"Открыт: {port}, ждём загрузку GRBL (~2 с)…")

    def _grbl_hello(self, port: str, streamer: GrblStreamer) -> None:
        if self._streamer is not streamer or self._job_total:
            return  # порт сменился или задание уже идёт — не мешаем
        streamer.send("$$")  # настройки → размеры поля
        streamer.send("$H")  # хоминг
        QTimer.singleShot(2500, lambda: self._check_grbl_alive(port, streamer))

    def _check_grbl_alive(self, port: str, streamer: GrblStreamer) -> None:
        """Порт открылся, но ни одного ok за 2.5 с — там не GRBL, закрываем."""
        if self._streamer is not streamer:
            return
        if streamer.ok_count == 0 and not self._job_total:
            self._mark_port(port, "· не отвечает")
            self._set_status(f"{port}: не отвечает — похоже, там не GRBL. "
                             "Выбери другой порт")
            self._close_connection()
        elif streamer.ok_count:
            self._set_status(f"GRBL на {port}, хоминг…")

    def _close_connection(self) -> None:
        if self._streamer:
            self._streamer.send_realtime(b"\x18")
            self._streamer.pump()
        if self._transport:
            self._transport.close()
        self._streamer = None
        self._transport = None
        self._connected_port = None
        self._set_run_state("idle")

    def _jog(self, dx: int, dy: int) -> None:
        if self._streamer is None or self._job_total:
            return  # во время задания jog запрещён — не сбивать счётчик строк
        step = self._jog_step.value()
        self._streamer.send_many([f"G91 G0 X{dx * step:g} Y{dy * step:g}", "G90"])

    def _enqueue(self, line: str) -> None:
        if self._streamer:
            self._streamer.send(line)

    def _set_pen_checked(self, checked: bool) -> None:
        """Синхронизировать кнопку PEN без повторной отправки команд."""
        self._pen_btn.blockSignals(True)
        self._pen_btn.setChecked(checked)
        self._pen_btn.blockSignals(False)

    def _toggle_pen(self, down: bool) -> None:
        """PEN: ручное опускание/поднимание пера (M3 S / M5)."""
        if self._streamer is None or self._job_total:
            self._set_pen_checked(False)  # пером рулит GCode либо некому слать
            return
        self._streamer.send(f"M3 S{self._pen_spin.value()}" if down else "M5")
        self._set_status("Перо опущено" if down else "Перо поднято")

    def _realtime(self, data: bytes) -> None:
        if self._streamer:
            self._streamer.send_realtime(data)

    def _soft_reset(self) -> None:
        """Стоп: сброс, перо вверх и сразу в хоминг."""
        if self._streamer:
            self._streamer.cancel_queue()
            self._streamer.send_realtime(b"\x18")
            self._job_total = 0
            self._job_sent = False
            self.canvas.clear_job_overlay()
            self.progress.hide()
            self._set_run_state("idle")
            self._set_pen_checked(False)  # перо вверх (без отправки — M5 пойдёт ниже)
            # GRBL после soft reset инициализируется не мгновенно — даём паузу,
            # затем поднимаем перо и уезжаем в homing
            streamer = self._streamer

            def _after_reset():
                if self._streamer is streamer and streamer is not None:
                    streamer.send_many(["M5", "$H"])

            QTimer.singleShot(800, _after_reset)
            self._set_status("Стоп: перо вверх, еду в $H")

    # -- параметры ---------------------------------------------------------------------

    def _params(self) -> CutParams:
        return CutParams(feed_mm_min=self._feed_spin.value(),
                         passes=self._passes_spin.value())

    def _tool(self) -> SpindlePen:
        return SpindlePen(down_s=self._pen_spin.value())

    # -- таймеры --------------------------------------------------------------------------

    def _pump(self) -> None:
        if self._streamer:
            self._streamer.pump()
            # смотревой: очередь стоит, а ok давно не было — так «замирание»
            # видно сразу, а не молча
            if (self._run_state in ("running", "homing") and self._streamer.busy
                    and time.monotonic() - self._last_ok > 5.0):
                self._set_status("GRBL не отвечает >5 с — очередь стоит "
                                 "(Стоп или проверь станок)")

    def _poll(self) -> None:
        if self._streamer:
            self._streamer.send_realtime(b"?")

    def _on_status(self, st) -> None:
        pos = st.mpos or st.wpos
        if self._job_total and pos:
            # подсветка ползёт по реальной позиции головки
            self.canvas.update_job_position(pos[0], pos[1])
            return
        if self.canvas.editing:
            return  # не перетираем подсказку режима правки в статусбаре
        pos_txt = f"X{pos[0]:.1f} Y{pos[1]:.1f}" if pos else ""
        if not self._job_total:
            self._set_status(f"{st.state} {pos_txt}")

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
