"""
AceCutter A30 - Controller v3.1
Приложение для управления плоттером через USB (115200 бод)
Протокол v3.1: STARTHOME, HOME, CALIBRATE, MOVE X Y S, PEN UP/DOWN, STATUS, STOP
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import serial
import serial.tools.list_ports
import threading
import time
import math
import re
import xml.etree.ElementTree as ET

# ===================== КЛАСС ПРИЛОЖЕНИЯ ==================================
class PlotterController:
    def __init__(self, root):
        self.root = root
        self.root.title("AceCutter A30 - Controller v3.1")
        self.root.geometry("1200x800")

        # Серийное соединение
        self.serial_connection = None
        self.is_connected = False

        # Состояние плоттера
        self.plotter_ready = False
        self.current_x = 0.0
        self.current_y = 0.0
        self.canvas_x_mm = 280.0
        self.canvas_y_mm = 400.0
        self.calibrated = False

        # Данные файла
        self.current_file = None
        self.file_type = None
        self.drawing_points = []
        self.svg_paths = []

        # Настройки вида
        self.scale_factor = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.rotation = 90
        self.last_x = 0
        self.last_y = 0
        self.is_panning = False

        # Размеры поля плоттера
        self.plotter_width_mm = 200   # X ось
        self.plotter_height_mm = 250  # Y ось

        self.setup_ui()
        self.refresh_ports()

    def setup_ui(self):
        # Основной фрейм
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ============ ЛЕВАЯ ПАНЕЛЬ ============
        left_panel = ttk.Frame(main_frame, width=400)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        left_panel.pack_propagate(False)

        # --- Подключение ---
        conn_frame = ttk.LabelFrame(left_panel, text="Подключение", padding=10)
        conn_frame.pack(fill=tk.X, pady=(0, 5))

        conn_row = ttk.Frame(conn_frame)
        conn_row.pack(fill=tk.X)

        ttk.Label(conn_row, text="Порт:").pack(side=tk.LEFT, padx=(0, 5))
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(conn_row, textvariable=self.port_var, width=15)
        self.port_combo.pack(side=tk.LEFT, padx=(0, 5))
        
        ttk.Button(conn_row, text="⟳", width=3, command=self.refresh_ports).pack(side=tk.LEFT, padx=(0, 5))
        
        self.connect_btn = ttk.Button(conn_row, text="Подключить", command=self.toggle_connection)
        self.connect_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.status_label = ttk.Label(conn_row, text="Отключено", foreground="red")
        self.status_label.pack(side=tk.LEFT)

        # --- Управление плоттером ---
        ctrl_frame = ttk.LabelFrame(left_panel, text="Управление плоттером", padding=10)
        ctrl_frame.pack(fill=tk.X, pady=(0, 5))

        # Homing
        home_label = ttk.Label(ctrl_frame, text="Homing:")
        home_label.pack(anchor=tk.W)
        
        home_btns = ttk.Frame(ctrl_frame)
        home_btns.pack(fill=tk.X, pady=(2, 5))
        ttk.Button(home_btns, text="STARTHOME", command=self.cmd_starthome).pack(side=tk.LEFT, padx=2)
        ttk.Button(home_btns, text="HOME", command=self.cmd_home).pack(side=tk.LEFT, padx=2)
        ttk.Button(home_btns, text="CALIBRATE", command=self.cmd_calibrate).pack(side=tk.LEFT, padx=2)

        # Jog
        jog_label = ttk.Label(ctrl_frame, text="Перемещение (мм):")
        jog_label.pack(anchor=tk.W)
        
        jog_frame = ttk.Frame(ctrl_frame)
        jog_frame.pack(fill=tk.X, pady=(2, 5))
        
        ttk.Button(jog_frame, text="X-10", command=lambda: self.cmd_jog(-10, 0)).grid(row=0, column=0, padx=2, pady=2)
        ttk.Button(jog_frame, text="X-1", command=lambda: self.cmd_jog(-1, 0)).grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(jog_frame, text="X+1", command=lambda: self.cmd_jog(1, 0)).grid(row=0, column=2, padx=2, pady=2)
        ttk.Button(jog_frame, text="X+10", command=lambda: self.cmd_jog(10, 0)).grid(row=0, column=3, padx=2, pady=2)
        
        ttk.Button(jog_frame, text="Y-10", command=lambda: self.cmd_jog(0, -10)).grid(row=1, column=0, padx=2, pady=2)
        ttk.Button(jog_frame, text="Y-1", command=lambda: self.cmd_jog(0, -1)).grid(row=1, column=1, padx=2, pady=2)
        ttk.Button(jog_frame, text="Y+1", command=lambda: self.cmd_jog(0, 1)).grid(row=1, column=2, padx=2, pady=2)
        ttk.Button(jog_frame, text="Y+10", command=lambda: self.cmd_jog(0, 10)).grid(row=1, column=3, padx=2, pady=2)

        # Перо
        pen_label = ttk.Label(ctrl_frame, text="Перо:")
        pen_label.pack(anchor=tk.W)
        
        pen_frame = ttk.Frame(ctrl_frame)
        pen_frame.pack(fill=tk.X, pady=(2, 5))
        ttk.Button(pen_frame, text="ВВЕРХ", command=self.cmd_pen_up).pack(side=tk.LEFT, padx=2)
        ttk.Button(pen_frame, text="ВНИЗ", command=self.cmd_pen_down).pack(side=tk.LEFT, padx=2)
        ttk.Button(pen_frame, text="STOP", command=self.cmd_stop).pack(side=tk.LEFT, padx=2)

        # Статус плоттера
        status_frame = ttk.Frame(ctrl_frame)
        status_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(status_frame, text="Запросить статус", command=self.cmd_status).pack(side=tk.LEFT, padx=2)

        # --- Лог ---
        log_frame = ttk.LabelFrame(left_panel, text="Лог", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED, height=12)
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # --- Ручная отправка ---
        send_frame = ttk.Frame(left_panel)
        send_frame.pack(fill=tk.X)
        
        self.cmd_entry = ttk.Entry(send_frame)
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.cmd_entry.bind('<Return>', lambda e: self.send_manual())
        
        ttk.Button(send_frame, text="Отправить", command=self.send_manual).pack(side=tk.RIGHT, padx=(5, 0))

        # ============ ПРАВАЯ ПАНЕЛЬ ============
        right_panel = ttk.LabelFrame(main_frame, text="Просмотр и редактирование")
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # --- Панель инструментов ---
        toolbar = ttk.Frame(right_panel)
        toolbar.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(toolbar, text="Файл:").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(toolbar, text="Загрузить SVG", command=self.load_svg).pack(side=tk.LEFT, padx=(0, 5))
        
        # Масштаб
        ttk.Label(toolbar, text="Масштаб:").pack(side=tk.LEFT, padx=(20, 5))
        self.scale_var = tk.DoubleVar(value=1.0)
        ttk.Spinbox(toolbar, from_=0.1, to=5.0, increment=0.1, textvariable=self.scale_var, 
                     width=5, command=self.update_canvas).pack(side=tk.LEFT, padx=(0, 10))
        
        # Поворот
        ttk.Label(toolbar, text="Поворот:").pack(side=tk.LEFT, padx=(0, 5))
        self.rotate_var = tk.DoubleVar(value=90.0)
        ttk.Spinbox(toolbar, from_=-180, to=180, increment=1, textvariable=self.rotate_var,
                     width=5, command=self.update_canvas).pack(side=tk.LEFT, padx=(0, 10))
        
        ttk.Button(toolbar, text="Сброс вида", command=self.reset_view).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(toolbar, text="Отправить на плоттер", command=self.send_to_plotter).pack(side=tk.RIGHT)

        # --- Холст ---
        self.canvas_frame = ttk.Frame(right_panel)
        self.canvas_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.canvas = tk.Canvas(self.canvas_frame, bg='white', highlightthickness=1, highlightbackground='gray')
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # События мыши
        self.canvas.bind("<Configure>", lambda e: self.update_canvas())
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_press)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_release)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)

    # ===================== ЛОГИРОВАНИЕ ===================================
    def log(self, msg):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    # ===================== СОЕДИНЕНИЕ ====================================
    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo['values'] = ports
        if ports:
            self.port_combo.set(ports[0])

    def toggle_connection(self):
        if not self.is_connected:
            self.connect()
        else:
            self.disconnect()

    def connect(self):
        port = self.port_var.get()
        if not port:
            messagebox.showerror("Ошибка", "Выберите порт!")
            return

        try:
            self.serial_connection = serial.Serial(port, 115200, timeout=1)
            self.is_connected = True
            self.connect_btn.config(text="Отключить")
            self.status_label.config(text="Подключено", foreground="green")
            self.log(f"Подключено к {port}")
            
            # Поток чтения
            self.read_thread = threading.Thread(target=self.read_serial, daemon=True)
            self.read_thread.start()
            
            # Ждём готовности плоттера
            self.root.after(3000, lambda: self.send("STATUS"))
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def disconnect(self):
        if self.serial_connection:
            self.serial_connection.close()
        self.is_connected = False
        self.connect_btn.config(text="Подключить")
        self.status_label.config(text="Отключено", foreground="red")
        self.log("Отключено")

    def send(self, cmd):
        if not self.is_connected or not self.serial_connection:
            self.log("Нет подключения!")
            return False
        try:
            full_cmd = cmd + '\n'
            self.serial_connection.write(full_cmd.encode('ascii'))
            self.log(f"→ {cmd}")
            return True
        except Exception as e:
            self.log(f"Ошибка отправки: {e}")
            return False

    def read_serial(self):
        while self.is_connected and self.serial_connection:
            try:
                if self.serial_connection.in_waiting:
                    line = self.serial_connection.readline().decode('ascii', errors='ignore').strip()
                    if line:
                        self.root.after(0, self.parse_response, line)
            except:
                break

    def parse_response(self, resp):
        if not resp:
            return
        
        self.log(f"← {resp}")
        
        if "READY" in resp:
            self.plotter_ready = True
            self.log("*** Плоттер готов к работе ***")
        
        elif resp.startswith("STATUS"):
            # Парсим все поля
            x = re.search(r'X=([\d.]+)', resp)
            y = re.search(r'Y=([\d.]+)', resp)
            pen = re.search(r'Pen=(\w+)', resp)
            left = re.search(r'Left=(\d)', resp)
            right = re.search(r'Right=(\d)', resp)
            cal = re.search(r'Calibrated=(\w+)', resp)
            cx = re.search(r'CanvasX=([\d.]+)', resp)
            cy = re.search(r'CanvasY=([\d.]+)', resp)
            
            if x: self.current_x = float(x.group(1))
            if y: self.current_y = float(y.group(1))
            if cx: self.canvas_x_mm = float(cx.group(1))
            if cy: self.canvas_y_mm = float(cy.group(1))
            if cal: self.calibrated = (cal.group(1) == "YES")
            
            self.plotter_width_mm = self.canvas_x_mm
            self.plotter_height_mm = self.canvas_y_mm
            
        elif "DONE" in resp:
            self.log("Движение завершено")
            self.send("STATUS")
        
        elif "LIMIT" in resp:
            self.log("*** СРАБОТАЛ КОНЦЕВИК ***")
        
        elif "CALIB SAVED" in resp or "CALIB LOADED" in resp:
            x = re.search(r'X=([\d.]+)', resp)
            y = re.search(r'Y=([\d.]+)', resp)
            if x: 
                self.canvas_x_mm = float(x.group(1))
                self.plotter_width_mm = self.canvas_x_mm
            if y: 
                self.canvas_y_mm = float(y.group(1))
                self.plotter_height_mm = self.canvas_y_mm

    # ===================== КОМАНДЫ ПЛОТТЕРУ ==============================
    def cmd_starthome(self):
        if self.send("STARTHOME"):
            self.log("Запущен первичный homing (250мм)...")

    def cmd_home(self):
        if self.send("HOME"):
            self.log("Быстрый homing...")

    def cmd_calibrate(self):
        if self.send("CALIBRATE"):
            self.log("Калибровка стола...")

    def cmd_jog(self, dx, dy):
        cmd = f"MOVE {dx} {dy} 500"
        self.send(cmd)

    def cmd_pen_up(self):
        self.send("PEN UP")

    def cmd_pen_down(self):
        self.send("PEN DOWN")

    def cmd_stop(self):
        self.send("STOP")

    def cmd_status(self):
        self.send("STATUS")

    def send_manual(self):
        cmd = self.cmd_entry.get().strip()
        if cmd:
            self.send(cmd)
            self.cmd_entry.delete(0, tk.END)

    # ===================== ЗАГРУЗКА ФАЙЛОВ ===============================
    def load_svg(self):
        filename = filedialog.askopenfilename(
            title="Выберите SVG файл",
            filetypes=[("SVG files", "*.svg"), ("All files", "*.*")]
        )
        if filename:
            try:
                self.current_file = filename
                self.file_type = "SVG"
                self.svg_paths = self.parse_svg(filename)
                self.drawing_points = self.extract_drawing_points()
                self.update_canvas()
                self.log(f"Загружен: {filename} ({len(self.svg_paths)} элементов)")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить SVG: {str(e)}")

    def parse_svg(self, filename):
        paths = []
        tree = ET.parse(filename)
        root = tree.getroot()
        
        for elem in root.iter():
            tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
            
            if tag == 'path':
                d = elem.get('d', '')
                if d:
                    # Простой парсинг: извлекаем координаты из команд M и L
                    coords = re.findall(r'[ML]\s*([-\d.]+)\s*([-\d.]+)', d)
                    points = [(float(x), float(y)) for x, y in coords]
                    if points:
                        paths.append(('path', points, elem.get('stroke', 'black')))
            
            elif tag == 'rect':
                x = float(elem.get('x', 0))
                y = float(elem.get('y', 0))
                w = float(elem.get('width', 0))
                h = float(elem.get('height', 0))
                paths.append(('rect', (x, y, w, h), elem.get('stroke', 'blue')))
            
            elif tag == 'circle':
                cx = float(elem.get('cx', 0))
                cy = float(elem.get('cy', 0))
                r = float(elem.get('r', 0))
                paths.append(('circle', (cx, cy, r), elem.get('stroke', 'red')))
            
            elif tag == 'line':
                x1 = float(elem.get('x1', 0))
                y1 = float(elem.get('y1', 0))
                x2 = float(elem.get('x2', 0))
                y2 = float(elem.get('y2', 0))
                paths.append(('line', (x1, y1, x2, y2), elem.get('stroke', 'green')))
            
            elif tag == 'polyline' or tag == 'polygon':
                pts_str = elem.get('points', '')
                if pts_str:
                    nums = pts_str.replace(',', ' ').split()
                    points = []
                    for i in range(0, len(nums)-1, 2):
                        points.append((float(nums[i]), float(nums[i+1])))
                    paths.append(('polyline', points, elem.get('stroke', 'purple')))
        
        return paths

    def extract_drawing_points(self):
        """Извлекает все точки для резки"""
        points = []
        for item_type, data, _ in self.svg_paths:
            if item_type == 'path' or item_type == 'polyline':
                points.extend(data)
            elif item_type == 'rect':
                x, y, w, h = data
                points.extend([(x,y), (x+w,y), (x+w,y+h), (x,y+h), (x,y)])
            elif item_type == 'circle':
                cx, cy, r = data
                for angle in range(0, 360, 15):
                    rad = math.radians(angle)
                    points.append((cx + r*math.cos(rad), cy + r*math.sin(rad)))
            elif item_type == 'line':
                x1, y1, x2, y2 = data
                points.extend([(x1,y1), (x2,y2)])
        return points

    # ===================== ОТОБРАЖЕНИЕ ====================================
    def update_canvas(self):
        self.canvas.delete("all")
        
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        cx, cy = cw // 2, ch // 2

        # Масштаб для отображения
        mm_to_px = min(cw / self.plotter_height_mm * 0.9, ch / self.plotter_width_mm * 0.9)

        # Поле плоттера
        fw = self.plotter_height_mm * mm_to_px
        fh = self.plotter_width_mm * mm_to_px
        
        x0 = cx - fw/2
        y0 = cy - fh/2
        x1 = cx + fw/2
        y1 = cy + fh/2

        self.canvas.create_rectangle(x0, y0, x1, y1, outline="red", width=2)
        
        # Оси
        self.canvas.create_line(x1, y0, x1, y1, fill="black", width=2)  # Y
        self.canvas.create_line(x0, y1, x1, y1, fill="black", width=2)  # X
        
        # Нулевая точка (0,0) в правом нижнем углу
        self.canvas.create_oval(x1-5, y1-5, x1+5, y1+5, fill="red")
        self.canvas.create_text(x1-15, y1-10, text="0,0", fill="red")

        # Сетка
        step = 10 * mm_to_px
        for i in range(int(self.plotter_height_mm/10) + 1):
            gx = x1 - i*step
            self.canvas.create_line(gx, y0, gx, y1, fill="#e0e0e0")
            if i % 5 == 0:
                self.canvas.create_text(gx, y1+12, text=str(i*10), fill="#888", font=("Arial", 8))

        for j in range(int(self.plotter_width_mm/10) + 1):
            gy = y1 - j*step
            self.canvas.create_line(x0, gy, x1, gy, fill="#e0e0e0")
            if j % 5 == 0:
                self.canvas.create_text(x1+18, gy, text=str(j*10), fill="#888", font=("Arial", 8))

        # Отрисовка контента
        if self.svg_paths:
            self.draw_content(cx, cy, mm_to_px)

    def draw_content(self, center_x, center_y, mm_to_px):
        scale = self.scale_var.get()
        rotation = math.radians(self.rotate_var.get())

        for item_type, data, color in self.svg_paths:
            if item_type == 'path' or item_type == 'polyline':
                scaled = [(x*scale, y*scale) for x, y in data]
                canvas_pts = []
                for px, py in scaled:
                    # Поворот и смещение
                    cx_px = center_y + (py - self.plotter_height_mm/2 + self.offset_x/10) * mm_to_px
                    cy_px = center_x - (px - self.plotter_width_mm/2 + self.offset_y/10) * mm_to_px
                    canvas_pts.extend([cx_px, cy_px])
                if len(canvas_pts) >= 4:
                    self.canvas.create_line(canvas_pts, fill=color, width=2)

            elif item_type == 'rect':
                x, y, w, h = data
                pts = [(x*scale, y*scale), ((x+w)*scale, y*scale), 
                       ((x+w)*scale, (y+h)*scale), (x*scale, (y+h)*scale), (x*scale, y*scale)]
                canvas_pts = []
                for px, py in pts:
                    canvas_pts.extend([
                        center_y + (py - self.plotter_height_mm/2) * mm_to_px,
                        center_x - (px - self.plotter_width_mm/2) * mm_to_px
                    ])
                self.canvas.create_line(canvas_pts, fill=color, width=2)

            elif item_type == 'circle':
                cx_svg, cy_svg, r = data
                cx_px = center_y + ((cy_svg*scale) - self.plotter_height_mm/2) * mm_to_px
                cy_px = center_x - ((cx_svg*scale) - self.plotter_width_mm/2) * mm_to_px
                r_px = r * scale * mm_to_px
                self.canvas.create_oval(cx_px-r_px, cy_px-r_px, cx_px+r_px, cy_px+r_px, 
                                       outline=color, width=2)

            elif item_type == 'line':
                x1, y1, x2, y2 = data
                cx1 = center_y + ((y1*scale) - self.plotter_height_mm/2) * mm_to_px
                cy1 = center_x - ((x1*scale) - self.plotter_width_mm/2) * mm_to_px
                cx2 = center_y + ((y2*scale) - self.plotter_height_mm/2) * mm_to_px
                cy2 = center_x - ((x2*scale) - self.plotter_width_mm/2) * mm_to_px
                self.canvas.create_line(cx1, cy1, cx2, cy2, fill=color, width=2)

    def reset_view(self):
        self.scale_var.set(1.0)
        self.rotate_var.set(90.0)
        self.offset_x = 0
        self.offset_y = 0
        self.update_canvas()

    def on_mouse_press(self, event):
        self.last_x = event.x
        self.last_y = event.y
        self.is_panning = True

    def on_mouse_drag(self, event):
        if self.is_panning:
            dx = (event.x - self.last_x) * 5
            dy = (event.y - self.last_y) * 5
            self.offset_x += dx / 3
            self.offset_y -= dy / 3
            self.last_x = event.x
            self.last_y = event.y
            self.update_canvas()

    def on_mouse_release(self, event):
        self.is_panning = False

    def on_mouse_wheel(self, event):
        if event.delta > 0:
            self.scale_var.set(min(5.0, self.scale_var.get() * 1.1))
        else:
            self.scale_var.set(max(0.1, self.scale_var.get() / 1.1))

    # ===================== ОТПРАВКА НА ПЛОТТЕР ============================
    def send_to_plotter(self):
        if not self.is_connected:
            messagebox.showwarning("Предупреждение", "Подключитесь к плоттеру!")
            return

        if not self.drawing_points:
            messagebox.showwarning("Предупреждение", "Нет данных для отправки!")
            return

        self.log("Отправка файла на плоттер...")
        
        # Конвертируем точки в команды
        commands = []
        first_point = True
        
        for x_mm, y_mm in self.drawing_points:
            if first_point:
                # Первая точка: подъезд с поднятым пером
                commands.append(f"MOVE {x_mm:.1f} {y_mm:.1f} 500")
                commands.append("PEN DOWN")
                first_point = False
            else:
                commands.append(f"MOVE {x_mm:.1f} {y_mm:.1f} 300")
        
        commands.append("PEN UP")
        commands.append("MOVE 0 0 500")
        
        # Отправляем в отдельном потоке
        def send_thread():
            for cmd in commands:
                if not self.is_connected:
                    break
                self.send(cmd)
                time.sleep(0.1)
            self.log("Отправка завершена!")
        
        threading.Thread(target=send_thread, daemon=True).start()


# ===================== ЗАПУСК ============================================
def main():
    root = tk.Tk()
    app = PlotterController(root)
    root.mainloop()

if __name__ == "__main__":
    main()