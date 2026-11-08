/*
 * Эмулятор андроид-головы для режущего плоттера Hoco / AceCutter A30.
 *
 * Шлёт родному телу init-последовательность и программы резки в формате
 * штатного приложения com.mobilefilmprinter. Протокол полностью описан
 * в ../protocol.md, все тайминги и форматы оттуда.
 *
 * Плата (выбирается автоматически):
 *   ESP32-C3: HardwareSerial1. RX=GPIO3 <- TX тела, TX=GPIO2 -> RX тела
 *            (распиновка как у esp32c3_uart_bridge, разъём J4).
 *            Board: ESP32C3 Dev Module, USB CDC On Boot: Enabled.
 *   Arduino Nano (AVR): SoftwareSerial. RX=D5 <- TX тела, TX=D6 -> RX тела.
 *            10000 бод SoftwareSerial держит, но впритык; ESP32 надёжнее.
 *
 * Команды через Serial Monitor (115200):
 *   1  тестовая разметка (квадрат 50x50 мм, стрелки X/Y)
 *   2  надпись PWNED BY / ROTTEN MECHANISM (векторный шрифт)
 *   3  окружность диаметром 50 мм (генерируется на лету)
 *   4  квадрат 50x50 мм
 *   i  повторная инициализация тела
 *   r  перезагрузка тела (;BD:31,0 дважды)
 *   o  выключение тела (;BD:31,1 четырежды)
 *
 * Ответы тела (RCMD=...) печатаются в Serial по мере поступления.
 * После программы ждём RCMD=10,1 (тело приняло) и RCMD=10,0 (тело закончило).
 */

#include <Arduino.h>

#if defined(ARDUINO_ARCH_ESP32)
  HardwareSerial Head(1);
  constexpr int PIN_RX_FROM_HEAD = 3;   // GPIO3 <- TX тела
  constexpr int PIN_TX_TO_HEAD   = 2;   // GPIO2 -> RX тела
#else
  #include <SoftwareSerial.h>
  SoftwareSerial Head(5, 6);            // RX=D5, TX=D6
#endif

constexpr uint32_t HEAD_BAUD = 10000;
constexpr uint32_t USB_BAUD  = 115200;

// --- Константы протокола (protocol.md) ---------------------------------
//
// Сигнатура пакета. Формула: "VSflr2wvcn" + md5("flr2wvcn"+DEVICE_ID+KEY),
// DEVICE_KEY="m3ocka03go8zsiws". Ниже сигнатура родной головы этого
// плоттера; для чужого DEVICE_ID она пересчитывается (см. protocol.md 6.2).
const char SIGNATURE[] = "VSflr2wvcn89792d3b3959bd8ed790976a5daf51c4";

// Парковка в конце программы, как в дампах приложения.
const char PARKING[] = "R1,0 A0,0 A1,0 R0,0";

// Таймауты ожидания событий от тела, мс.
constexpr uint32_t T_ACK_MS  = 5000;     // RCMD=10,1 после пакета
constexpr uint32_t T_DONE_MS = 600000;   // RCMD=10,0, длинный рез тоже дождёмся

// Рабочее поле тела в юнитах (40 юнитов на мм).
constexpr int X_MAX = 15000;   // подача
constexpr int Y_MAX = 10000;   // каретка

// --- Слой UART ----------------------------------------------------------

void headBegin() {
#if defined(ARDUINO_ARCH_ESP32)
  Head.begin(HEAD_BAUD, SERIAL_8N1, PIN_RX_FROM_HEAD, PIN_TX_TO_HEAD);
  Head.setRxBufferSize(1024);
#else
  Head.begin(HEAD_BAUD);
#endif
}

// Команда вида ;BD:<code>[,<args>];\n
void sendCmd(const char* code, const char* args = nullptr) {
  Head.print(";BD:");
  Head.print(code);
  if (args) { Head.print(','); Head.print(args); }
  Head.print(F(";\n"));
  Serial.print(F("[TX] ;BD:"));
  Serial.print(code);
  if (args) { Serial.print(','); Serial.print(args); }
  Serial.println(';');
}

// Побайтовая выкладка строки из PROGMEM (на ESP32 PROGMEM пустой).
void writePgm(const char* p) {
  while (uint8_t c = pgm_read_byte(p++)) Head.write(c);
}

// --- Приём RCMD ----------------------------------------------------------

constexpr size_t RCMD_BUF = 96;
char rcmdBuf[RCMD_BUF];
size_t rcmdLen = 0;
char lastRcmd[RCMD_BUF] = "";

// Неблокирующе читает UART, готовый кадр печатает и запоминает в lastRcmd.
// Возвращает 1, если в этом вызове был получен целый кадр.
int pollRcmd() {
  while (Head.available()) {
    char c = (char)Head.read();
    if (c == '\n' || c == '\r') continue;
    if (rcmdLen + 1 < RCMD_BUF) rcmdBuf[rcmdLen++] = c;
    if (c == ';') {
      rcmdBuf[rcmdLen] = 0;
      strcpy(lastRcmd, rcmdBuf);
      Serial.print(F("[RX] "));
      Serial.println(rcmdBuf);
      rcmdLen = 0;
      return 1;
    }
  }
  return 0;
}

// Ждёт кадр, начинающийся с prefix. Побочно логирует всё прилетевшее.
bool awaitRcmd(const char* prefix, uint32_t timeoutMs) {
  uint32_t t0 = millis();
  while (millis() - t0 < timeoutMs) {
    if (pollRcmd()) {
      if (strncmp(lastRcmd, prefix, strlen(prefix)) == 0) return true;
    } else {
      delay(2);
    }
  }
  Serial.print(F("[!] timeout waiting "));
  Serial.println(prefix);
  return false;
}

// Подобрать и напечатать всё, что тело ещё не доплёвывает.
void drainRcmd(uint32_t ms) {
  uint32_t t0 = millis();
  while (millis() - t0 < ms) { pollRcmd(); delay(2); }
}

// --- Инициализация тела (protocol.md, раздел 8) --------------------------

// Паузы соответствуют родному трафику (PlotterControl.INIT_SEQUENCE).
void initSequence(int pressure = 48, int runSpeed = 100, int cutSpeed = 20) {
  Serial.println(F("=== init ==="));
  sendCmd("11");                                delay(200);
  sendCmd("30", "1");                           delay(1140);
  sendCmd("50", String(pressure).c_str());      delay(1460);
  sendCmd("66", "15000,15145,10000,9980");      delay(1800);
  sendCmd("62", String(runSpeed).c_str());      delay(780);
  sendCmd("54", "255");                         delay(200);
  sendCmd("56", String(cutSpeed).c_str());      delay(1260);
  sendCmd("70", "15,75");                       delay(1100);
  sendCmd("11");                                delay(100);
  drainRcmd(300);   // хвосты ответов до печати итога
  Serial.println(F("=== init done ==="));
}

// --- Каркас программы резки ----------------------------------------------

// Пакет: сигнатура, IN IN, тестовый штрих 2 мм, тело, парковка, конец.
// Тело передаётся колбэком: текст программы льётся из PROGMEM, окружность
// и текст генерируются на лету. Всё уходит одним потоком байтов.
typedef void (*BodyFn)();

void runProgram(BodyFn body, const char* name) {
  Serial.print(F("=== program: "));
  Serial.println(name);
  delay(1000);   // пауза после init, как у приложения
  rcmdLen = 0; lastRcmd[0] = 0;

  Head.print(SIGNATURE);
  Head.print(F(" IN IN "));
  Head.print(F("R0,0 A0,80 "));   // тестовый штрих, традиция приложения

  body();

  Head.print(' ');
  Head.print(PARKING);
  Head.print(F(" @\n"));

  Serial.println(F("[TX] program sent"));
  awaitRcmd("RCMD=10,1", T_ACK_MS);    // тело приняло пакет
  awaitRcmd("RCMD=10,0", T_DONE_MS);   // тело закончило рез
  Serial.println(F("=== program done ==="));
}

// --- Мини-векторный шрифт -------------------------------------------------
//
// Сетка 4x6 на букву, координаты в клетках, отрезки полилиниями.
// Межбуквенный интервал 2 клетки. Этого набора хватает на надпись
// PWNED BY ROTTEN MECHANISM; при нужде глиф добавляется одной строкой.

struct Seg { int8_t x, y; };            // точка полилинии в клетках
struct Glyph { const Seg* pts; uint8_t n; };  // одна полилиния
struct GlyphSet { const Glyph* lines; uint8_t n; };

// Глифы: каждая буква одна-три полилинии, все точки лежат во флеше.

const Seg A1[] PROGMEM = {{0,0},{0,4},{2,6},{4,4},{4,0}};
const Seg A2[] PROGMEM = {{0,2},{4,2}};
const Seg B1[] PROGMEM = {{0,0},{0,6},{3,6},{4,5},{4,4},{3,3},{0,3}};
const Seg B2[] PROGMEM = {{3,3},{4,2},{4,1},{3,0},{0,0}};
const Seg C1[] PROGMEM = {{4,6},{0,6},{0,0},{4,0}};
const Seg D1[] PROGMEM = {{0,0},{0,6},{2,6},{4,4},{4,2},{2,0},{0,0}};
const Seg E1[] PROGMEM = {{4,6},{0,6},{0,0},{4,0}};
const Seg E2[] PROGMEM = {{0,3},{3,3}};
const Seg H1[] PROGMEM = {{0,0},{0,6}};
const Seg H2[] PROGMEM = {{4,0},{4,6}};
const Seg H3[] PROGMEM = {{0,3},{4,3}};
const Seg I1[] PROGMEM = {{1,6},{3,6}};
const Seg I2[] PROGMEM = {{2,6},{2,0}};
const Seg I3[] PROGMEM = {{1,0},{3,0}};
const Seg M1[] PROGMEM = {{0,0},{0,6},{2,3},{4,6},{4,0}};
const Seg N1[] PROGMEM = {{0,0},{0,6},{4,0},{4,6}};
const Seg O1[] PROGMEM = {{0,0},{0,6},{4,6},{4,0},{0,0}};
const Seg P1[] PROGMEM = {{0,0},{0,6},{3,6},{4,5},{4,4},{3,3},{0,3}};
const Seg R1[] PROGMEM = {{0,0},{0,6},{3,6},{4,5},{4,4},{3,3},{0,3}};
const Seg R2[] PROGMEM = {{2,3},{4,0}};
const Seg S1[] PROGMEM = {{4,6},{0,6},{0,3},{4,3},{4,0},{0,0}};
const Seg T1[] PROGMEM = {{0,6},{4,6}};
const Seg T2[] PROGMEM = {{2,6},{2,0}};
const Seg W1[] PROGMEM = {{0,6},{0,0},{2,3},{4,0},{4,6}};
const Seg Y1[] PROGMEM = {{0,6},{2,3},{4,6}};
const Seg Y2[] PROGMEM = {{2,3},{2,0}};

struct GlyphDef { char ch; const Seg* segs[3]; uint8_t per[3]; };
// per[i] = число точек в полилинии i, 0 = полилинии нет.

const GlyphDef GLYPHS[] PROGMEM = {
  {'A', {A1, A2, nullptr}, {5,2,0}},
  {'B', {B1, B2, nullptr}, {7,5,0}},
  {'C', {C1, nullptr, nullptr}, {4,0,0}},
  {'D', {D1, nullptr, nullptr}, {7,0,0}},
  {'E', {E1, E2, nullptr}, {4,2,0}},
  {'H', {H1, H2, H3}, {2,2,2}},
  {'I', {I1, I2, I3}, {2,2,2}},
  {'M', {M1, nullptr, nullptr}, {5,0,0}},
  {'N', {N1, nullptr, nullptr}, {4,0,0}},
  {'O', {O1, nullptr, nullptr}, {5,0,0}},
  {'P', {P1, nullptr, nullptr}, {7,0,0}},
  {'R', {R1, R2, nullptr}, {7,2,0}},
  {'S', {S1, nullptr, nullptr}, {6,0,0}},
  {'T', {T1, T2, nullptr}, {2,2,0}},
  {'W', {W1, nullptr, nullptr}, {5,0,0}},
  {'Y', {Y1, Y2, nullptr}, {3,2,0}},
};

constexpr uint8_t N_GLYPHS = sizeof(GLYPHS) / sizeof(GLYPHS[0]);

// Рисует текст в поток программы. scale = юнитов на клетку.
// Базовая точка (x0,y0) это левый нижний угол первой буквы.
void drawText(const char* text, int x0, int y0, int scale) {
  int x = x0;
  for (const char* p = text; *p; p++) {
    char c = *p;
    if (c == ' ') { x += 6 * scale; continue; }
    GlyphDef gd;
    bool found = false;
    for (uint8_t i = 0; i < N_GLYPHS; i++) {
      memcpy_P(&gd, &GLYPHS[i], sizeof(gd));
      if (gd.ch == c) { found = true; break; }
    }
    if (!found) { x += 6 * scale; continue; }   // неизвестный глиф: пропуск
    for (uint8_t li = 0; li < 3 && gd.per[li]; li++) {
      Seg segs[8];
      memcpy_P(segs, gd.segs[li], gd.per[li] * sizeof(Seg));
      Head.print('R');
      Head.print(x + segs[0].x * scale);
      Head.print(',');
      Head.print(y0 + segs[0].y * scale);
      Head.print(' ');
      for (uint8_t k = 1; k < gd.per[li]; k++) {
        Head.print('A');
        Head.print(x + segs[k].x * scale);
        Head.print(',');
        Head.print(y0 + segs[k].y * scale);
        Head.print(' ');
      }
    }
    x += 6 * scale;   // 4 буква + 2 интервал
  }
}

// --- Тела программ --------------------------------------------------------

// Тестовая разметка: квадрат 50x50 мм, стрелки осей, цифра 4, метки X и Y.
const char PROG_MARKUP[] PROGMEM =
  "R1000,1000 A6000,1000 A6000,6000 A1000,6000 A1000,1000 "
  "R2000,800 A7000,800 A6500,1300 R7000,800 A6500,300 "
  "R3500,2000 A3500,5000 A3000,4500 R3500,5000 A4000,4500 "
  "R3000,3000 A3000,4000 A4000,3500 R3000,3500 A4000,3000 "
  "R7500,1000 A8500,2000 R8500,1000 A7500,2000 "
  "R2500,5500 A2000,6000 R2000,5500 A2250,5750";

// Квадрат 50x50 мм с отступом 5 мм.
const char PROG_SQUARE[] PROGMEM =
  "R200,200 A5200,200 A5200,5200 A200,5200 A200,200";

void bodyMarkup() { writePgm(PROG_MARKUP); }
void bodySquare() { writePgm(PROG_SQUARE); }

// Надпись двумя строками. Высота буквы 6 клеток: scale 40 даёт 240 юнитов
// (6 мм). Строка ROTTEN MECHANISM: 16 знаков * 240 = 3840 юнитов по X.
void bodyPwned() {
  drawText("PWNED BY", 500, 4000, 40);
  drawText("ROTTEN MECHANISM", 500, 3000, 40);
}

// Окружность 50 мм, 64 сегмента, потоково.
void bodyCircle() {
  const int cx = 1040, cy = 1040, r = 1000;
  Head.print('R');
  Head.print(cx + r); Head.print(','); Head.print(cy); Head.print(' ');
  for (int i = 1; i <= 64; i++) {
    float a = (TWO_PI * i) / 64.0f;
    Head.print('A');
    Head.print(cx + (int)lroundf(r * cosf(a)));
    Head.print(',');
    Head.print(cy + (int)lroundf(r * sinf(a)));
    Head.print(' ');
  }
}

// --- Управление питанием тела ---------------------------------------------

void rebootBody() {
  Serial.println(F("=== reboot body ==="));
  for (int i = 0; i < 2; i++) { sendCmd("31", "0"); delay(2020); }
}

void powerOffBody() {
  Serial.println(F("=== power off body ==="));
  for (int i = 0; i < 4; i++) { sendCmd("31", "1"); delay(250); }
}

// --- Setup / loop -----------------------------------------------------------

void setup() {
  Serial.begin(USB_BAUD);
  headBegin();
  delay(1500);   // ESP32-C3 при открытии порта перезагружается
  Serial.println(F("Head emulator for Hoco/AceCutter A30, protocol in protocol.md"));
  Serial.println(F("1 markup | 2 pwned | 3 circle50 | 4 square | i init | r reboot | o off"));
  initSequence();
}

void loop() {
  pollRcmd();   // фоновая печать всего, что шлёт тело

  if (Serial.available()) {
    char c = Serial.read();
    switch (c) {
      case '1': runProgram(bodyMarkup, "markup 50mm");  break;
      case '2': runProgram(bodyPwned,  "PWNED BY ROTTEN MECHANISM"); break;
      case '3': runProgram(bodyCircle, "circle d50mm"); break;
      case '4': runProgram(bodySquare, "square 50mm");  break;
      case 'i': initSequence(); break;
      case 'r': rebootBody();   break;
      case 'o': powerOffBody(); break;
      default: break;
    }
    while (Serial.available()) Serial.read();
  }
  delay(2);
}
