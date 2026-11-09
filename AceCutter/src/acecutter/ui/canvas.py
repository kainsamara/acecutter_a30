"""Канвас: рабочее поле-полоса (ширина X фиксирована, длина Y — валик),
контуры — перетаскиваемые объекты. Y инвертируется (в Qt ось Y вниз).

Управление: левая — выбор/перетаскивание контуров (рамкой — мультивыбор),
правая по контуру — контекстное меню (центр/в ноль/вписать/изменить),
правая по пустому — панорама, колесо — листание рулона (зума нет).

Режим «Изменить»: рамка с 8 хэндлами, тянешь — масштаб относительно
противоположной стороны, Shift лочит пропорции. Выход: ПКМ/Enter — ок,
Esc — отмена. Текущий размер показывается в статусбаре (сигнал sizeHint).
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QMenu,
)

from ..domain.document import Workspace
from ..domain.geometry import Transform
from ..domain.layout import HANDLES, handle_scale
from ..domain.shapes import Polyline

_CONTOUR = 0   # userData: исходная полилиния в мм
_HANDLE = 1    # userData: имя хэндла

_HANDLE_CURSORS = {
    # курсоры режима МАСШТАБА; в режиме поворота углы получают CrossCursor
    "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
    "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
    "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
}
_ANGLE_HANDLES = frozenset(("tl", "tr", "bl", "br"))
_ROT_SNAP_DEG = 15.0  # Shift при повороте — шаг 15°
# поворотный режим рамки: янтарь, чтобы глаз сразу отличал от масштаба
_ROT_FRAME = "#d9962b"
_ROT_HPEN = "#8a5a00"
_ROT_HBRUSH = "#f0c987"
_HANDLE_SIZE = 2.4  # мм


class CanvasView(QGraphicsView):
    ctxAction = Signal(str)   # 'center' | 'origin' | 'fit' | 'resize'
    sizeHint = Signal(str)    # текст в нижнюю строку приложения

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # когда сцена уже окна — прижимаем её к левому нижнему углу,
        # чтобы ноль поля не уплывал в центр
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self._workspace: Workspace | None = None
        self._panning = False
        self._pan_pos = QPointF()
        self._did_first_fit = False
        self._refit_pending = False
        # состояние режима «Изменить»
        self._edit_items: list[QGraphicsItem] = []
        self._edit_orig: list[Polyline] = []
        self._edit_orig0: list[Polyline] = []
        self._edit_dirty = False
        self._edit_bounds: tuple[float, float, float, float] | None = None
        self._edit_t = Transform()
        self._handles: dict[str, QGraphicsItem] = {}
        self._edit_frame: QGraphicsItem | None = None
        self._active_handle: str | None = None
        # режим правки: "scale" | "rotate" | None. Переключается двойным
        # кликом внутри правки (_toggle_edit_mode), живёт до выхода из неё
        self._edit_mode: str | None = None
        # вращение за угловой хэндл (режим правки): центр/радиус/старт-угол в мм
        self._rot_center: tuple[float, float] | None = None
        self._rot_radius = 0.0
        self._rot_start_angle = 0.0  # радианы, мм-система (CCW+, Y вверх)
        self._rot_arrow: QGraphicsItem | None = None
        self._rot_label: QGraphicsItem | None = None
        # визуализация задания
        self._job_paths: list[dict] = []
        self._job_ci = 0
        self._job_si = 0

    # -- ввод --------------------------------------------------------------

    def wheelEvent(self, event) -> None:  # noqa: N802
        # зума нет: сетка всегда по ширине окна; колесо листает рулон
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - event.angleDelta().y() * 2)
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._workspace is None:
            return
        # центр запоминаем только после первого показа — до него сцена ещё
        # не вписана и mapToScene вернёт мусор
        center = (self.mapToScene(self.viewport().rect().center())
                  if self._did_first_fit else None)
        if not self._refit_pending:
            self._refit_pending = True
            QTimer.singleShot(0, lambda: self._refit(center))

    def _refit(self, center) -> None:
        self._refit_pending = False
        self.fit_width()
        if not self._did_first_fit:
            self._did_first_fit = True  # fit_width ставит ноль к нижнему краю
        elif center is not None:
            self.centerOn(center)  # вертикальную позицию не дёргаем

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if self.editing:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.apply_scale_edit()
                e.accept()
                return
            if e.key() == Qt.Key.Key_Escape:
                self.cancel_scale_edit()
                e.accept()
                return
        super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.editing:
            # двойной клик в правке: масштаб ⇄ поворот
            self._toggle_edit_mode()
            e.accept()
            return
        # двойной клик по контуру = «Изменить» (как Enter и пункт меню)
        if e.button() == Qt.MouseButton.LeftButton and not self.editing:
            item = self.itemAt(e.position().toPoint())
            if item is not None and item.data(_CONTOUR) is not None:
                if not item.isSelected():
                    self._scene.clearSelection()
                    item.setSelected(True)
                self.enter_scale_edit()
                e.accept()
                return
        super().mouseDoubleClickEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.RightButton:
            if self.editing:
                self._edit_menu(e.globalPos())
            else:
                item = self.itemAt(e.position().toPoint())
                if item is not None and item.data(_CONTOUR) is not None:
                    self._contour_menu(e.globalPos(), item)
                else:
                    self._start_pan(e)
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton and self.editing:
            h = self._handle_at(e.position().toPoint())
            if h:
                self._active_handle = h
                if h in _ANGLE_HANDLES and self._edit_mode == "rotate":
                    p = self.mapToScene(e.position().toPoint())
                    self._init_rotate(p.x(), -p.y())
            e.accept()
            return
        super().mousePressEvent(e)

    def _init_rotate(self, mx: float, my: float) -> None:
        """Инициализация протяжки-поворота от центра ТЕКУЩИХ bounds (в мм)."""
        x0, y0, x1, y1 = self._edit_bounds
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        self._rot_center = (cx, cy)
        self._rot_radius = math.hypot(mx - cx, my - cy)
        self._rot_start_angle = math.atan2(my - cy, mx - cx)

    def _toggle_edit_mode(self) -> None:
        """Двойной клик в правке: масштаб ⇄ поворот (фичи разделены)."""
        self._edit_mode = "rotate" if self._edit_mode != "rotate" else "scale"
        self._drop_rotate_visual()
        self._build_edit_decor()
        if self._edit_mode == "rotate":
            self.sizeHint.emit(f"Поворот: тяни за углы (Shift — шаг "
                               f"{_ROT_SNAP_DEG:.0f}°) · двойной клик — размер "
                               "· ПКМ/Enter — ок · Esc — отмена")
        else:
            self.sizeHint.emit("Размер: тяни рамку (Shift — пропорции) · "
                               "двойной клик — поворот · ПКМ/Enter — ок · Esc — отмена")

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._panning:
            d = self._pan_pos - e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + int(d.y()))
            self._pan_pos = e.position()
            e.accept()
            return
        if self._active_handle:
            p = self.mapToScene(e.position().toPoint())
            shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if (self._edit_mode == "rotate"
                    and self._active_handle in _ANGLE_HANDLES):
                self._rotate_from_mm(p.x(), -p.y(), snap=shift)
            else:
                self._scale_from_mm(p.x(), -p.y(), proportional=shift)
            e.accept()
            return
        if self.editing and e.buttons() == Qt.MouseButton.LeftButton:
            e.accept()  # в режиме правки контуры не таскаются
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.RightButton and self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton and self._active_handle:
            self._active_handle = None
            self._drop_rotate_visual()
            self._bake_scale()  # рамка и хэндлы остаются вокруг нового размера
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def _start_pan(self, e) -> None:
        self._panning = True
        self._pan_pos = e.position()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    # -- контекстные меню ----------------------------------------------------

    def _contour_menu(self, global_pos, item: QGraphicsItem) -> None:
        """ПКМ по вектору: действия с выделением (в скобках — хоткеи)."""
        if not item.isSelected():
            self._scene.clearSelection()
            item.setSelected(True)
        menu = QMenu(self)
        menu.addAction("⇔ Центр по ширине (C)", lambda: self.ctxAction.emit("center"))
        menu.addAction("↙ В ноль (X)", lambda: self.ctxAction.emit("origin"))
        menu.addAction("↔ Вписать в ширину (V)", lambda: self.ctxAction.emit("fit"))
        menu.addSeparator()
        menu.addAction("⟳ Повернуть (R)", lambda: self.ctxAction.emit("rotate"))
        menu.addAction("✥ Изменить (Enter)", lambda: self.ctxAction.emit("resize"))
        menu.addSeparator()
        menu.addAction("🗑 Удалить (Del)", self.delete_selected)
        menu.exec(global_pos)

    def _edit_menu(self, global_pos) -> None:
        menu = QMenu(self)
        menu.addAction("✓ Ок (Enter)", self.apply_scale_edit)
        menu.addAction("✕ Отмена (Esc)", self.cancel_scale_edit)
        menu.exec(global_pos)

    # -- режим «Изменить» (интерактивный масштаб) ----------------------------

    @property
    def editing(self) -> bool:
        return bool(self._edit_items)

    def enter_scale_edit(self) -> None:
        items = [it for it in self.contour_items() if it.isSelected()]
        if not items:
            self.sizeHint.emit("Сначала выдели контур (клик или рамка)")
            return
        self._edit_orig = [self._item_polyline(it) for it in items]
        self._edit_orig0 = list(self._edit_orig)  # снапшот для «Отмена»
        self._edit_dirty = False
        self._edit_t = Transform()
        self._edit_mode = "scale"  # двойной клик в правке сменит на поворот
        self._edit_items = items  # recreate удалит их и создаст управляемые копии
        for it in self.contour_items():
            it.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._recreate_edit_items(self._edit_orig)
        self.setFocus()
        self.sizeHint.emit("Тяни рамку — размер (Shift — пропорции) · "
                           "двойной клик — поворот · ПКМ/Enter — ок · Esc — отмена")

    def _edit_bounds_of(self, polys: list[Polyline]):
        rects = [b for b in (pl.bounds() for pl in polys) if b]
        if not rects:
            return None
        return (min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[2] for r in rects), max(r[3] for r in rects))

    def _recreate_edit_items(self, polys: list[Polyline]) -> None:
        """Пересоздать редактируемые контуры + рамку/хэндлы вокруг них."""
        for it in self._edit_items:
            self._scene.removeItem(it)
        pen = QPen(QColor("#1a6cff"), 0.0)
        self._edit_items = []
        for pl in polys:
            item = self._scene.addPath(self._path_of(pl), pen)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            item.setData(_CONTOUR, pl)
            item.setSelected(True)
            self._edit_items.append(item)
        self._edit_orig = polys
        self._edit_bounds = self._edit_bounds_of(polys)
        self._edit_t = Transform()
        self._build_edit_decor()

    def _build_edit_decor(self) -> None:
        """Рамка и хэндлы вокруг текущего _edit_bounds.

        Режим виден глазами: масштаб — сине-зелёная рамка и все 8 хэндлов,
        поворот — янтарная рамка и только 4 угла (рёбра спрятаны).
        """
        for r in self._handles.values():
            self._scene.removeItem(r)
        self._handles = {}
        if self._edit_frame is not None:
            self._scene.removeItem(self._edit_frame)
        x0, y0, x1, y1 = self._edit_bounds
        rot = self._edit_mode == "rotate"
        frame_pen = QPen(QColor(_ROT_FRAME if rot else "#2e7d9e"),
                         0.0, Qt.PenStyle.DashLine)
        self._edit_frame = self._scene.addRect(
            QRectF(x0, -y1, x1 - x0, y1 - y0), frame_pen)
        self._edit_frame.setZValue(9)
        hpen = QPen(QColor(_ROT_HPEN if rot else "#1d4f63"), 0.0)
        hbrush = QColor(_ROT_HBRUSH if rot else "#7fc4e8")
        # квадратик живёт в (0,0), позиция — только setPos: иначе rect+pos
        # складываются и хэндл улетает «правее и выше» при протяжке
        hrect = QRectF(-_HANDLE_SIZE / 2, -_HANDLE_SIZE / 2,
                       _HANDLE_SIZE, _HANDLE_SIZE)
        for name in HANDLES:
            hx, hy = self._handle_scene_pos(name)
            r = self._scene.addRect(hrect, hpen, hbrush)
            r.setPos(hx, hy)
            r.setZValue(10)
            r.setData(_HANDLE, name)
            r.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            if rot:
                if name not in _ANGLE_HANDLES:
                    r.hide()  # в повороте крутим только за углы
                else:
                    r.setCursor(Qt.CursorShape.CrossCursor)
            else:
                r.setCursor(_HANDLE_CURSORS[name])
            self._handles[name] = r

    def _handle_scene_pos(self, name: str) -> tuple[float, float]:
        x0, y0, x1, y1 = self._edit_bounds
        x = x1 if "r" in name else (x0 if "l" in name else (x0 + x1) / 2)
        y = y1 if "t" in name else (y0 if "b" in name else (y0 + y1) / 2)
        return x, -y

    def _handle_at(self, pos) -> str | None:
        item = self.itemAt(pos)
        if item is None:
            return None
        name = item.data(_HANDLE)
        return name if name else None

    def _scale_from_mm(self, mx: float, my: float, proportional: bool) -> None:
        sx, sy, px, py = handle_scale(self._edit_bounds, self._active_handle,
                                      mx, my, proportional=proportional)
        self._edit_t = (Transform.translate(px, py)
                        @ Transform.scale(sx, sy)
                        @ Transform.translate(-px, -py))
        # превью на живых айтемах: трансформ в scene-координатах (Y флипнут)
        qt = QTransform(sx, 0.0, 0.0, sy, px * (1.0 - sx), py * (sy - 1.0))
        for it in self._edit_items:
            it.setTransform(qt)
        self._edit_frame.setTransform(qt)
        # прочие хэндлы прячем, активный едет за курсором
        for name, r in self._handles.items():
            if name == self._active_handle:
                r.setPos(mx, -my)
                r.show()
            else:
                r.hide()
        w0 = self._edit_bounds[2] - self._edit_bounds[0]
        h0 = self._edit_bounds[3] - self._edit_bounds[1]
        self.sizeHint.emit(f"Ш {w0 * sx:.1f} × В {h0 * sy:.1f} мм"
                           + (" · пропорции" if proportional else ""))
        self._edit_dirty = True

    def _rotate_from_mm(self, mx: float, my: float, *, snap: bool) -> None:
        """Угловой хэндл в протяжке: поворот вокруг центра bounds + дуга-стрелка."""
        cx, cy = self._rot_center
        theta = math.degrees(math.atan2(my - cy, mx - cx) - self._rot_start_angle)
        if snap:
            theta = round(theta / _ROT_SNAP_DEG) * _ROT_SNAP_DEG
        self._edit_t = (Transform.translate(cx, cy)
                        @ Transform.rotate(theta)
                        @ Transform.translate(-cx, -cy))
        # превью на живых айтемах: сцена Y вниз, поэтому вокруг (cx,-cy)
        # вращаем на -theta (сопряжение флипа Y меняет знак угла)
        qt = QTransform()
        qt.translate(cx, -cy)
        qt.rotate(-theta)
        qt.translate(-cx, cy)
        for it in self._edit_items:
            it.setTransform(qt)
        self._edit_frame.setTransform(qt)
        # активный хэндл едет по окружности, остальные спрятаны
        r = self._rot_radius
        a = math.atan2(my - cy, mx - cx)
        for name, h in self._handles.items():
            if name == self._active_handle:
                h.setPos(cx + r * math.cos(a), -(cy + r * math.sin(a)))
                h.show()
            else:
                h.hide()
        self._show_rotate_visual(theta)
        d = ((-theta + 180.0) % 360.0) - 180.0  # юзеру: по часовой — плюс
        self.sizeHint.emit(f"Поворот {d:+.0f}°"
                           + (f" (шаг {_ROT_SNAP_DEG:.0f}°)" if snap else ""))
        self._edit_dirty = True

    def _show_rotate_visual(self, theta_deg: float) -> None:
        """Дуга-стрелка от центра к хэндлу + подпись градусов.

        Дуга строится полилинией по точкам: семантика углов arcTo в
        Y-вниз системе — минное поле, а тут всё детерминировано.
        """
        cx, cy = self._rot_center
        r = max(self._rot_radius * 0.8, 6.0)
        a0 = -self._rot_start_angle       # scene-углы = минус мм-углы (Y флип)
        sweep = -math.radians(theta_deg)  # мм CCW+ → scene: по часовой плюс
        cx_s, cy_s = cx, -cy

        def pt(a: float) -> QPointF:
            return QPointF(cx_s + r * math.cos(a), cy_s + r * math.sin(a))

        path = QPainterPath(pt(a0))
        nseg = 48
        for i in range(1, nseg + 1):
            path.lineTo(pt(a0 + sweep * i / nseg))
        # наконечник в конце дуги вдоль касательной по направлению движения
        ae = a0 + sweep
        sgn = 1.0 if sweep >= 0.0 else -1.0
        tx, ty = -math.sin(ae) * sgn, math.cos(ae) * sgn
        nx, ny = math.cos(ae), math.sin(ae)
        ex, ey = cx_s + r * math.cos(ae), cy_s + r * math.sin(ae)
        tip, half_w = 3.0, 1.1
        path.moveTo(ex + tx * tip, ey + ty * tip)
        path.lineTo(ex + nx * half_w, ey + ny * half_w)
        path.lineTo(ex - nx * half_w, ey - ny * half_w)
        path.closeSubpath()
        if self._rot_arrow is None:
            self._rot_arrow = self._scene.addPath(path, QPen(QColor("#d9962b"), 0.0))
            self._rot_arrow.setZValue(11)
        else:
            self._rot_arrow.setPath(path)
        # подпись с градусами — у середины дуги, чуть дальше радиуса
        d = ((-theta_deg + 180.0) % 360.0) - 180.0
        mid = a0 + sweep / 2.0
        if self._rot_label is None:
            self._rot_label = QGraphicsSimpleTextItem(f"{d:+.0f}°")
            f = QFont("Segoe UI", 9)
            f.setBold(True)
            self._rot_label.setFont(f)
            self._rot_label.setBrush(QColor("#8a5a00"))
            self._rot_label.setZValue(11)
            self._scene.addItem(self._rot_label)
        else:
            self._rot_label.setText(f"{d:+.0f}°")
        self._rot_label.setPos(cx_s + (r + 2.5) * math.cos(mid),
                               cy_s + (r + 2.5) * math.sin(mid))

    def _drop_rotate_visual(self) -> None:
        """Убрать дугу и подпись (отпускание/выход из правки)."""
        if self._rot_arrow is not None:
            self._scene.removeItem(self._rot_arrow)
            self._rot_arrow = None
        if self._rot_label is not None:
            self._scene.removeItem(self._rot_label)
            self._rot_label = None
        self._rot_center = None

    def _bake_scale(self) -> None:
        """Отпустили хэндл — запечь превью: рамка и хэндлы переезжают
        на новое место вокруг растянутых векторов, не возвращаясь назад."""
        if not self._edit_dirty:
            return
        polys = [pl.transformed(self._edit_t) for pl in self._edit_orig]
        self._recreate_edit_items(polys)
        self._edit_dirty = False

    def apply_scale_edit(self) -> None:
        if not self.editing:
            return
        self._bake_scale()  # если тянули и не отпустили — печём перед выходом
        self._finish_edit()
        self.sizeHint.emit("Масштаб применён")

    def cancel_scale_edit(self) -> None:
        if not self.editing:
            return
        self._recreate_edit_items(self._edit_orig0)  # вернуть как было
        self._finish_edit()
        self.sizeHint.emit("Изменение отменено")

    def _finish_edit(self) -> None:
        self._drop_rotate_visual()
        self._edit_mode = None
        for r in self._handles.values():
            self._scene.removeItem(r)
        self._handles = {}
        if self._edit_frame is not None:
            self._scene.removeItem(self._edit_frame)
        self._edit_frame = None
        self._edit_items = []
        self._edit_orig = []
        self._edit_orig0 = []
        self._edit_bounds = None
        self._edit_t = Transform()
        self._edit_dirty = False
        self._active_handle = None
        for it in self.contour_items():
            it.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

    # -- визуализация выполнения задания --------------------------------------

    def begin_job_overlay(self, polys: list[Polyline]) -> None:
        """Слой подсветки резки: контуры в порядке резки, пустые и скрытые.
        Дорисовываются частями по реальной позиции головки (update_job_position)."""
        self.clear_job_overlay()
        pen = QPen(QColor("#e74c3c"), 0.0)
        for pl in polys:
            if not pl.points:
                continue
            item = self._scene.addPath(QPainterPath(), pen)
            item.setZValue(5)
            item.hide()
            self._job_paths.append({"points": pl.points, "closed": pl.closed,
                                    "item": item})

    def update_job_position(self, x_mm: float, y_mm: float) -> None:
        """Головка реально здесь (MPos/WPos из статуса) — дорисовать красный
        путь до неё. Продвижение монотонное: ищем ближайшую точку маршрута
        в окне вперёд от текущего указателя."""
        if not self._job_paths:
            return
        head = (x_mm, y_mm)
        best: tuple[float, int, int] | None = None
        n = 0
        ci = self._job_ci
        while ci < len(self._job_paths) and n < 600:
            pts = self._job_paths[ci]["points"]
            si = self._job_si if ci == self._job_ci else 0
            while si < len(pts) and n < 600:
                d = math.dist(head, (pts[si].x, pts[si].y))
                if best is None or d < best[0]:
                    best = (d, ci, si)
                n += 1
                si += 1
            ci += 1
        if best is None or best[0] > 25.0:
            return  # голова далеко от маршрута (travel) — подсветку не дёргаем
        _, new_ci, bsi = best
        # пропущенные контуры — закрасить полностью
        for c in range(self._job_ci, new_ci):
            seg = self._job_paths[c]
            seg["item"].setPath(self._path_of(Polyline(seg["points"], seg["closed"])))
            seg["item"].show()
        # текущий — до проекции головки на активный сегмент
        seg = self._job_paths[new_ci]
        pts = seg["points"]
        last_idx = len(pts) - 1
        a = pts[bsi]
        b = pts[0] if (seg["closed"] and bsi == last_idx) else pts[min(bsi + 1, last_idx)]
        abx, aby = b.x - a.x, b.y - a.y
        ab2 = abx * abx + aby * aby
        t = 0.0 if ab2 < 1e-12 else max(0.0, min(1.0, ((x_mm - a.x) * abx + (y_mm - a.y) * aby) / ab2))
        px, py = a.x + t * abx, a.y + t * aby
        path = QPainterPath(QPointF(pts[0].x, -pts[0].y))
        for p in pts[1:bsi + 1]:
            path.lineTo(QPointF(p.x, -p.y))
        path.lineTo(QPointF(px, -py))
        seg["item"].setPath(path)
        seg["item"].show()
        self._job_ci, self._job_si = new_ci, bsi

    def clear_job_overlay(self) -> None:
        """Задание кончилось — всё снова синее."""
        for seg in self._job_paths:
            self._scene.removeItem(seg["item"])
        self._job_paths = []
        self._job_ci = 0
        self._job_si = 0

    # -- поле ----------------------------------------------------------------

    def set_workspace(self, ws: Workspace) -> None:
        self._workspace = ws
        self._rebuild_decor()

    def workspace(self) -> Workspace | None:
        return self._workspace

    def _rebuild_decor(self) -> None:
        ws = self._workspace
        for it in [i for i in self._scene.items() if i.data(_CONTOUR) is None]:
            self._scene.removeItem(it)
        if ws is None:
            return
        rect = QRectF(0.0, -ws.length_mm, ws.width_mm, ws.length_mm)
        self._scene.addRect(rect, QPen(QColor("#5a5a5a"), 0.0, Qt.PenStyle.SolidLine))
        grid = QPen(QColor("#b8b8b8"), 0.0, Qt.PenStyle.DashLine)   # мелкая — пунктир
        major = QPen(QColor("#909090"), 0.0, Qt.PenStyle.DashLine)  # каждая 5-я — темнее
        label_font = QFont("Segoe UI", 7)
        label_color = QColor("#7a7a7a")

        # адаптивный шаг сетки: на длинном рулоне не плодим тысячи линий
        grid_step = 10.0 if ws.length_mm <= 4000 else 50.0
        label_step = 50.0
        while ws.length_mm / label_step > 400:  # подписей не больше четырёх сотен
            label_step *= 2

        y = 0.0
        while y <= ws.length_mm + 1e-9:
            major_line = abs(y % (grid_step * 5)) < 1e-9
            self._scene.addLine(0.0, -y, ws.width_mm, -y,
                                major if major_line else grid)
            if abs(y % label_step) < 1e-9:
                t = self._scene.addText(f"{y:.0f}", label_font)
                t.setDefaultTextColor(label_color)
                t.setPos(1.5, -y - t.boundingRect().height())  # внутри поля
            y += grid_step
        x = 0.0
        while x <= ws.width_mm + 1e-9:
            major_line = abs(x % (grid_step * 5)) < 1e-9
            self._scene.addLine(x, 0.0, x, -ws.length_mm,
                                major if major_line else grid)
            if abs(x % label_step) < 1e-9:
                t = self._scene.addText(f"{x:.0f}", label_font)
                t.setDefaultTextColor(label_color)
                t.setPos(x - t.boundingRect().width() / 2, 1.5)  # под полем у Y=0
            x += grid_step
        for it in self._scene.items():
            if it.data(_CONTOUR) is None:
                it.setZValue(-1)
                it.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
                it.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self._update_scene_rect()

    # -- контуры ---------------------------------------------------------------

    def load_polylines(self, polylines: list[Polyline], *, color: str = "#1a6cff") -> None:
        if self.editing:
            self.cancel_scale_edit()
        self.clear_job_overlay()
        for it in [i for i in self._scene.items() if i.data(_CONTOUR) is not None]:
            self._scene.removeItem(it)
        pen = QPen(QColor(color), 0.0)
        for pl in polylines:
            if pl.points:
                self._add_contour(pl, pen)
        self._update_scene_rect()

    def add_polylines(self, polylines: list[Polyline], *, color: str = "#1a6cff") -> None:
        """Добавить контуры к уже лежащим на поле (повторный импорт)."""
        pen = QPen(QColor(color), 0.0)
        for pl in polylines:
            if pl.points:
                self._add_contour(pl, pen)
        self._update_scene_rect()

    def _path_of(self, pl: Polyline) -> QPainterPath:
        path = QPainterPath(QPointF(pl.points[0].x, -pl.points[0].y))
        for q in pl.points[1:]:
            path.lineTo(QPointF(q.x, -q.y))
        if pl.closed:
            path.closeSubpath()
        return path

    def _add_contour(self, pl: Polyline, pen: QPen) -> None:
        item = self._scene.addPath(self._path_of(pl), pen)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        item.setData(_CONTOUR, pl)

    def contour_items(self) -> list[QGraphicsItem]:
        return [i for i in self._scene.items() if i.data(_CONTOUR) is not None]

    def polylines(self) -> list[Polyline]:
        """Текущие контуры в мм с учётом перетаскивания мышью."""
        return [self._item_polyline(it) for it in self.contour_items()]

    def polylines_selected(self) -> list[Polyline]:
        """Выделенные контуры в мм (для действий контекстного меню)."""
        return [self._item_polyline(it) for it in self.contour_items()
                if it.isSelected()]

    def replace_selected(self, new_polys: list[Polyline]) -> int:
        """Заменить выделенные контуры новыми (столько же)."""
        doomed = [it for it in self.contour_items() if it.isSelected()]
        if not doomed or not new_polys:
            return 0
        for it in doomed:
            self._scene.removeItem(it)
        pen = QPen(QColor("#1a6cff"), 0.0)
        added = 0
        for pl in new_polys:
            if not pl.points:
                continue
            item = self._scene.addPath(self._path_of(pl), pen)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            item.setData(_CONTOUR, pl)
            item.setSelected(True)
            added += 1
        self._update_scene_rect()
        return added

    @staticmethod
    def _item_polyline(it: QGraphicsItem) -> Polyline:
        pl: Polyline = it.data(_CONTOUR)
        off = it.pos()
        if off.isNull():
            return pl
        # сцена: Y вниз; мм: Y вверх
        return pl.transformed(Transform.translate(off.x(), -off.y()))

    def selected_count(self) -> int:
        return sum(1 for it in self.contour_items() if it.isSelected())

    def delete_selected(self) -> int:
        """Удалить выделенные контуры. Возвращает сколько удалили."""
        if self.editing:
            self.cancel_scale_edit()
        doomed = [it for it in self.contour_items() if it.isSelected()]
        for it in doomed:
            self._scene.removeItem(it)
        self._update_scene_rect()
        return len(doomed)

    def select_all(self) -> None:
        for it in self.contour_items():
            it.setSelected(True)

    def clear_selection(self) -> None:
        self._scene.clearSelection()

    # -- навигация ---------------------------------------------------------------

    def _update_scene_rect(self) -> None:
        ws = self._workspace
        if ws:
            # поля вокруг разметки: слева 14 (зазор до нуля), справа 8,
            # сверху 8, снизу 24 — под цифры оси X; ноль не липнет к краю
            base = QRectF(-14.0, -(ws.length_mm + 8.0),
                          ws.width_mm + 22.0, ws.length_mm + 32.0)
        else:
            base = QRectF()
        rects = [base] + [it.sceneBoundingRect() for it in self.contour_items()]
        rect = rects[0]
        for r in rects[1:]:
            rect = rect.united(r)
        self._scene.setSceneRect(rect)

    def fit_width(self) -> None:
        """Поле по ширине окна; вокруг сетки — зазоры (сцена с полями)."""
        ws = self._workspace
        if ws is None:
            return
        self.resetTransform()
        sx = (self.viewport().width() * 0.96) / (ws.width_mm + 22.0)
        self.scale(sx, sx)
        vh_mm = self.viewport().height() / sx
        self.centerOn((self.viewport().width() / sx) / 2.0 - 14.0,
                      vh_mm / 2.0 - 6.0)

    def reveal_content(self) -> None:
        items = self.contour_items()
        if not items:
            self.fit_width()
            return
        rect = items[0].sceneBoundingRect()
        for it in items[1:]:
            rect = rect.united(it.sceneBoundingRect())
        self.centerOn(rect.center())
