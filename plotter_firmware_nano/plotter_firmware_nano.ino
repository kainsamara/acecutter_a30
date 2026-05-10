/*
 * Plotter MCU firmware — Arduino Nano + CNC Shield V3 (GRBL pinout).
 * Эмулирует MCU тела AceCutter A30 для общения с Android-головой.
 *
 * Подключения (стандартный GRBL pinout):
 *   D5  -> STEP X
 *   D2  -> DIR X
 *   D6  -> STEP Y
 *   D3  -> DIR Y
 *   D8  -> Stepper ENABLE (общий, активный LOW)
 *   D11 -> PEN (соленоид через MOSFET)        ← shield Z+
 *   D9  -> X_LIMIT (концевик каретки)         ← shield X-
 *   В4  -> Кнопка аварийной остановки         ← shield Z-DIR
 *   A4  -> SoftwareSerial RX от TX головы
 *
 *   D0/D1 (USB) — для отладки в Serial monitor 115200
 *
 * Питание:
 *   - VMOT (12-24 В) на shield для моторов
 *   - 5 В от Nano (через USB или VIN) питает логику shield
 *   - Голова и shield должны иметь общий GND
 *
 * Зависимости (Arduino IDE → Library Manager):
 *   - AccelStepper by Mike McCauley
 *
 * Прошивка: Arduino IDE → Board: "Arduino Nano" → Processor:
 *   "ATmega328P" (или Old Bootloader если новый не работает) → Upload.
 */

#include <AccelStepper.h>
#include <SoftwareSerial.h>

// ===================== Конфиг =========================================
constexpr int PIN_X_STEP = 5;
constexpr int PIN_X_DIR = 2;
constexpr int PIN_Y_STEP = 6;
constexpr int PIN_Y_DIR = 3;
constexpr int PIN_ENABLE = 8;  // active LOW
constexpr int PIN_ESTOP = 4;   // аварийная кнопка
constexpr int PIN_PEN = 11;
constexpr int PIN_X_LIMIT = 9;
constexpr int PIN_HEAD_RX = A5;  // SoftSerial RX
constexpr int PIN_HEAD_TX = A4;  // SoftSerial TX
constexpr int PIN_LED = 13;      // встроенный LED Nano

constexpr bool PEN_ACTIVE_HIGH = true;  // HIGH = опустить
constexpr bool X_LIMIT_ACTIVE_LOW = false;
constexpr bool INVERT_X_DIR = true;
constexpr bool INVERT_Y_DIR = true;

constexpr long HEAD_BAUD = 10000;
constexpr long USB_BAUD = 115200;

// Кинематика
constexpr int CANVAS_X = 280;                                      // максимальная дальность по X в мм
constexpr int CANVAS_Y = 400;                                      // максимальная дальность по Y в мм (валик, тут в теории бесконечно много)
constexpr float STEPS_PER_MM_X = 36.36f;                           // каретка
constexpr float STEPS_PER_MM_Y = 32.00f;                           // валик подачи
constexpr int UNITS_PER_MM = 40;                                   // 40 юнит = 1 мм (плоттер)
constexpr float STEPS_PER_UNIT_X = STEPS_PER_MM_X / UNITS_PER_MM;  // 2.0
constexpr float STEPS_PER_UNIT_Y = STEPS_PER_MM_Y / UNITS_PER_MM;

// Скорости (шагов/сек). 600 mm/min × 80 step/mm / 60 = 800 шаг/сек.
constexpr float MOVE_SPEED_SPS = 800.0f;
constexpr float CUT_SPEED_SPS = 400.0f;
constexpr float ACCEL_SPS2 = 2000.0f;

// Homing
constexpr long HOMING_MAX_STEPS_X = (long)(CANVAS_X * STEPS_PER_MM_X);  // макс ход по X
constexpr long HOMING_MAX_STEPS_Y = (long)(CANVAS_Y * STEPS_PER_MM_Y);
constexpr float HOMING_SEEK_SPS = 600.0f;
constexpr float HOMING_FEED_SPS = 300.0f;
constexpr long HOMING_PULL_OFF_STEPS_X = (long)(3 * STEPS_PER_MM_X);
constexpr long HOMING_PULL_OFF_STEPS_Y = (long)(3 * STEPS_PER_MM_Y);
constexpr bool HOMING_DIR_X_INVERT = false;

// Идентификация (то что MCU возвращает в RCMD=11)
const char DEVICE_SERIAL[] PROGMEM = "002400143439470C35303835";
const char DEVICE_VERSION[] PROGMEM = "6";

// Очередь движений
constexpr uint8_t QUEUE_CAP = 96;  // на ATmega328P 96 элементов = ~480 байт SRAM
struct MotionCmd {
  bool pen_down;
  int16_t x_units;
  int16_t y_units;
};
MotionCmd queueBuf[QUEUE_CAP];
volatile uint8_t qHead = 0, qTail = 0;

inline uint8_t qSize() {
  return (qTail - qHead) & (QUEUE_CAP - 1 < 0xFF ? 0xFF : 0xFF);
  // используем модуль:
}
inline uint8_t queueSize() {
  return (qTail >= qHead) ? (qTail - qHead) : (QUEUE_CAP - qHead + qTail);
}
inline bool queueEmpty() {
  return qHead == qTail;
}
bool queuePush(const MotionCmd& c) {
  uint8_t next = (qTail + 1) % QUEUE_CAP;
  if (next == qHead) return false;
  queueBuf[qTail] = c;
  qTail = next;
  return true;
}
bool queuePop(MotionCmd& c) {
  if (qHead == qTail) return false;
  c = queueBuf[qHead];
  qHead = (qHead + 1) % QUEUE_CAP;
  return true;
}

// ===================== Глобальные объекты ============================
AccelStepper stepperX(AccelStepper::DRIVER, PIN_X_STEP, PIN_X_DIR);
AccelStepper stepperY(AccelStepper::DRIVER, PIN_Y_STEP, PIN_Y_DIR);
SoftwareSerial headSerial(PIN_HEAD_RX, PIN_HEAD_TX);

bool penDown = false;
bool busy = false;
bool drawActive = false;
bool drawAck10_1Sent = false;
bool drawAck10_0Sent = true;

int pressureVal = 48;
int moveSpeedPct = 100;
int cutSpeedPct = 20;

// ===================== Вспомогательное ===============================
void setEnable(bool en) {
  digitalWrite(PIN_ENABLE, en ? LOW : HIGH);  // active LOW
}

void penUp() {
  digitalWrite(PIN_PEN, PEN_ACTIVE_HIGH ? LOW : HIGH);
  penDown = false;
  delay(15);
}
void penDownNow() {
  digitalWrite(PIN_PEN, PEN_ACTIVE_HIGH ? HIGH : LOW);
  penDown = true;
  delay(15);
}

inline bool xLimitTriggered() {
  int v = digitalRead(PIN_X_LIMIT);
  return X_LIMIT_ACTIVE_LOW ? (v == LOW) : (v == HIGH);
}

void applySpeedFromCmd(bool penDownNow_) {
  float pct = penDownNow_ ? cutSpeedPct : moveSpeedPct;
  float maxSps = penDownNow_ ? CUT_SPEED_SPS : MOVE_SPEED_SPS;
  float sps = maxSps * pct / 100.0f;
  if (sps < 50) sps = 50;
  stepperX.setMaxSpeed(sps);
  stepperY.setMaxSpeed(sps);
}

// ===================== Парсер протокола ==============================
// Шлёт RCMD ответ голове (в head UART).
void sendRcmd_P(const __FlashStringHelper* s) {
  headSerial.print(s);
  Serial.print(F("[ESP→HEAD] "));
  Serial.println(s);
}
void sendRcmdString(const String& s) {
  headSerial.print(s);
  Serial.print(F("[ESP→HEAD] "));
  Serial.println(s);
}

void handleBdCommand(int id, const String& args) {
  switch (id) {
    case 11:
      {
        String r = F("RCMD=11,");
        // serial из PROGMEM
        char buf[28];
        strcpy_P(buf, DEVICE_SERIAL);
        r += buf;
        r += ',';
        strcpy_P(buf, DEVICE_VERSION);
        r += buf;
        r += ';';
        sendRcmdString(r);
        break;
      }
    case 30:
      {
        // главный init — здесь оригинал делал self-homing и возвращал тики
        doHomeX();
        sendRcmdString(String(F("RCMD=30,0,")) + millis() + ';');
        break;
      }
    case 31:
      {
        // 31,0=reboot, 31,1=power off — просто сбросим состояние
        while (queuePop(*reinterpret_cast<MotionCmd*>(queueBuf))) {}  // не работает на queue, см. реальный clear
        qHead = qTail = 0;
        stepperX.stop();
        stepperY.stop();
        drawActive = false;
        busy = false;
        break;
      }
    case 50:
      {
        pressureVal = args.toInt();
        sendRcmdString(String(F("RCMD=51,")) + pressureVal + ';');
        break;
      }
    case 51: sendRcmdString(String(F("RCMD=51,")) + pressureVal + ';'); break;
    case 54: sendRcmdString(String(F("RCMD=54,")) + args + ';'); break;
    case 55: sendRcmd_P(F("RCMD=55,255;")); break;
    case 56:
      {
        cutSpeedPct = args.toInt();
        sendRcmdString(String(F("RCMD=57,")) + cutSpeedPct + ';');
        break;
      }
    case 57: sendRcmdString(String(F("RCMD=57,")) + cutSpeedPct + ';'); break;
    case 62:
      {
        moveSpeedPct = args.toInt();
        sendRcmdString(String(F("RCMD=63,")) + moveSpeedPct + ';');
        break;
      }
    case 63: sendRcmdString(String(F("RCMD=63,")) + moveSpeedPct + ';'); break;
    case 66: sendRcmdString(String(F("RCMD=67,")) + args + ';'); break;
    case 67: sendRcmd_P(F("RCMD=67,15000,15145,10000,9980;")); break;
    case 70: sendRcmdString(String(F("RCMD=71,")) + args + ';'); break;
    case 71: sendRcmd_P(F("RCMD=71,15,75;")); break;
    default: /* silent */ break;
  }
}

// Состояние парсера
enum ParserState {
  PS_IDLE,
  PS_BD_HEAD,    // прочитали ';', ждём 'B'
  PS_BD_BODY,    // ;BD: ... до ';'
  PS_DRAW_SIG,   // VS... до пробела
  PS_DRAW_BODY,  // токены IN, R, A, @
};
ParserState pState = PS_IDLE;
char parseBuf[40];
uint8_t parseLen = 0;

void parserAppend(char c) {
  if (parseLen < sizeof(parseBuf) - 1) {
    parseBuf[parseLen++] = c;
    parseBuf[parseLen] = 0;
  }
}
void parserReset() {
  parseLen = 0;
  parseBuf[0] = 0;
}

void handleDrawToken(const char* tok, uint8_t len) {
  if (len == 2 && tok[0] == 'I' && tok[1] == 'N') return;  // IN
  if (len == 1 && tok[0] == '@') {
    drawActive = false;
    drawAck10_0Sent = false;
    return;
  }
  if (len < 4) return;
  char c = tok[0];
  if (c != 'R' && c != 'A') return;

  // парсим x,y
  int comma = -1;
  for (uint8_t i = 1; i < len; i++)
    if (tok[i] == ',') {
      comma = i;
      break;
    }
  if (comma < 0) return;
  // временно вырезаем
  char tmp[20];
  uint8_t xn = comma - 1;
  if (xn >= sizeof(tmp)) return;
  memcpy(tmp, tok + 1, xn);
  tmp[xn] = 0;
  int x = atoi(tmp);

  uint8_t yn = len - comma - 1;
  if (yn >= sizeof(tmp)) return;
  memcpy(tmp, tok + comma + 1, yn);
  tmp[yn] = 0;
  int y = atoi(tmp);

  MotionCmd cmd;
  cmd.pen_down = (c == 'A');
  cmd.x_units = x;
  cmd.y_units = y;
  if (queuePush(cmd)) {
    if (!drawAck10_1Sent) {
      sendRcmd_P(F("RCMD=10,1;"));
      drawAck10_1Sent = true;
      drawAck10_0Sent = false;
    }
  } else {
    Serial.println(F("[!] queue overflow"));
  }
}

void parserFeed(uint8_t b) {
  switch (pState) {
    case PS_IDLE:
      if (b == ';') {
        pState = PS_BD_HEAD;
        parserReset();
      } else if (b == 'V') {
        pState = PS_DRAW_SIG;
        parserReset();
        parserAppend('V');
      } else if (b == 'I' || b == 'R' || b == 'A' || b == '@') {
        // программа без сигнатуры (от ПК)
        drawActive = true;
        drawAck10_1Sent = false;
        drawAck10_0Sent = false;
        pState = PS_DRAW_BODY;
        parserReset();
        parserAppend((char)b);
      }
      break;

    case PS_BD_HEAD:
      parserAppend((char)b);
      if (parseLen >= 3 && parseBuf[0] == 'B' && parseBuf[1] == 'D'
          && parseBuf[2] == ':') {
        pState = PS_BD_BODY;
        parserReset();
      } else if (parseLen >= 3) {
        pState = PS_IDLE;
      }
      break;

    case PS_BD_BODY:
      if (b == ';') {
        // распарсить id,args
        int comma = -1;
        for (uint8_t i = 0; i < parseLen; i++)
          if (parseBuf[i] == ',') {
            comma = i;
            break;
          }
        String args;
        int id;
        if (comma < 0) {
          id = atoi(parseBuf);
          args = "";
        } else {
          parseBuf[comma] = 0;
          id = atoi(parseBuf);
          args = String(parseBuf + comma + 1);
        }
        handleBdCommand(id, args);
        parserReset();
        pState = PS_IDLE;
      } else if (b != '\n' && b != '\r') {
        parserAppend((char)b);
        if (parseLen > sizeof(parseBuf) - 2) {
          pState = PS_IDLE;
          parserReset();
        }
      }
      break;

    case PS_DRAW_SIG:
      parserAppend((char)b);
      if (b == ' ' || b == '\t') {
        if (parseLen > 4 && parseBuf[0] == 'V' && parseBuf[1] == 'S') {
          drawActive = true;
          drawAck10_1Sent = false;
          drawAck10_0Sent = false;
          pState = PS_DRAW_BODY;
          parserReset();
        }
      } else if (parseLen > sizeof(parseBuf) - 2) {
        pState = PS_IDLE;
        parserReset();
      }
      break;

    case PS_DRAW_BODY:
      if (b == ' ' || b == '\t' || b == '\n' || b == '\r') {
        if (parseLen > 0) {
          handleDrawToken(parseBuf, parseLen);
          parserReset();
        }
      } else {
        parserAppend((char)b);
        if (parseLen > sizeof(parseBuf) - 2) parserReset();
      }
      break;
  }
}

void parserTick() {
  // когда draw закончилась И очередь опустела → шлём 10,0
  if (!drawActive && !drawAck10_0Sent && queueEmpty()
      && stepperX.distanceToGo() == 0 && stepperY.distanceToGo() == 0) {
    sendRcmd_P(F("RCMD=10,0;"));
    drawAck10_0Sent = true;
  }
}

// ===================== Кинематика ====================================
void startNextCmd() {
  MotionCmd cmd;
  if (!queuePop(cmd)) return;

  if (cmd.pen_down && !penDown) penDownNow();
  else if (!cmd.pen_down && penDown) penUp();

  applySpeedFromCmd(cmd.pen_down);

  long tx = (long)(cmd.x_units * STEPS_PER_UNIT_X);
  long ty = (long)(cmd.y_units * STEPS_PER_UNIT_Y);
  if (INVERT_X_DIR) tx = -tx;
  if (INVERT_Y_DIR) ty = -ty;

  stepperX.moveTo(tx);
  stepperY.moveTo(ty);
  busy = true;
}

void motionTick() {
  if (busy) {
    if (stepperX.distanceToGo() == 0 && stepperY.distanceToGo() == 0) {
      busy = false;
    }
  }
  if (!busy && !queueEmpty()) {
    startNextCmd();
  }
  stepperX.run();
  stepperY.run();
}

// ===================== Homing ========================================
void doHomeX() {
  Serial.println(F("[HOME] start X axis"));
  penUp();

  // Быстрый поиск концевика
  stepperX.setMaxSpeed(HOMING_SEEK_SPS);
  long dir = HOMING_DIR_X_INVERT ? -1 : 1;
  stepperX.move(dir * HOMING_MAX_STEPS_X);

  unsigned long t0 = millis();
  while (stepperX.distanceToGo() != 0) {
    stepperX.run();
    if (xLimitTriggered()) {
      stepperX.stop();
      while (stepperX.distanceToGo() != 0) stepperX.run();
      break;
    }
    if (millis() - t0 > 30000) {
      stepperX.stop();
      break;
    }
  }
  delay(80);

  // Отход от концевика (pull-off)
  stepperX.setMaxSpeed(HOMING_FEED_SPS);
  stepperX.move(-dir * HOMING_PULL_OFF_STEPS_X);
  while (stepperX.distanceToGo() != 0) stepperX.run();
  delay(50);

  // Финальный медленный подвод
  stepperX.move(dir * HOMING_PULL_OFF_STEPS_X * 2);
  while (stepperX.distanceToGo() != 0) {
    stepperX.run();
    if (xLimitTriggered()) {
      stepperX.stop();
      while (stepperX.distanceToGo() != 0) stepperX.run();
      break;
    }
  }
  delay(50);

  // Установка нулевой позиции
  stepperX.setCurrentPosition(0);
  stepperY.setCurrentPosition(0);
  stepperX.setMaxSpeed(MOVE_SPEED_SPS);
  stepperY.setMaxSpeed(MOVE_SPEED_SPS);
  Serial.println(F("[HOME] done"));
}

// ===================== Диагностика через USB =========================
String pcCmdBuf;
uint32_t lastPcByte = 0;

void execPcCommand(const String& cmd) {
  Serial.print(F("[CMD] "));
  Serial.println(cmd);
  if (cmd == "T" || cmd == "?") {
    Serial.print(F("[STATUS] X="));
    Serial.print(stepperX.currentPosition());
    Serial.print(F(" Y="));
    Serial.print(stepperY.currentPosition());
    Serial.print(F(" queue="));
    Serial.print(queueSize());
    Serial.print(F(" busy="));
    Serial.println(busy);
  } else if (cmd == "STOP") {
    // Мгновенная остановка с учетом ускорения
    stepperX.stop();
    stepperY.stop();

    // Очищаем очередь команд
    qHead = qTail = 0;

    // Принудительно останавливаем движение
    stepperX.move(0);
    stepperY.move(0);

    // Поднимаем перо
    if (penDown) penUp();

    // Сбрасываем флаги
    busy = false;
    drawActive = false;
    drawAck10_1Sent = false;
    drawAck10_0Sent = true;
    delay(100);

    // Устанавливаем текущую позицию как ту, где реально остановились, чтобы после STOP не было рывков
    stepperX.setCurrentPosition(stepperX.currentPosition());
    stepperY.setCurrentPosition(stepperY.currentPosition());

    Serial.println(F("[STOP] Motors stopped and queue cleared"));

  } else if (cmd == "HALT") {
    setEnable(false);  // отключаем питание моторов
    qHead = qTail = 0;
    busy = false;
    drawActive = false;
    Serial.println(F("[HALT] Motors disabled"));
    Serial.println(F("Use 'EN' to re-enable"));
  } else if (cmd == "PEN") {
    if (penDown) penUp();
    else penDownNow();
    Serial.println(penDown ? F("[PEN] DOWN") : F("[PEN] UP"));
  } else if (cmd == "HOME") {
    doHomeX();
  } else if (cmd.startsWith("X+") || cmd.startsWith("X-")) {
    long steps = 0;
    float distance_mm = 0;

    if (cmd == "X+") distance_mm = 1.0;  // 1 мм
    else if (cmd == "X-") distance_mm = -1.0;
    else if (cmd == "X++") distance_mm = 10.0;  // 10 мм
    else if (cmd == "X--") distance_mm = -10.0;
    else {
      // Парсим X+50, X-30 и т.д. - число в МИЛЛИМЕТРАХ
      float mm = cmd.substring(2).toFloat();
      if (cmd.startsWith("X+")) distance_mm = mm;
      else if (cmd.startsWith("X-")) distance_mm = -mm;
    }

    if (distance_mm != 0) {
      // переводим мм в шаги
      steps = (long)(distance_mm * STEPS_PER_MM_X);
      if (INVERT_X_DIR) steps = -steps;
      stepperX.setMaxSpeed(MOVE_SPEED_SPS);
      stepperX.move(steps);
      busy = true;
      Serial.print(F("[JOG X] "));
      Serial.print(distance_mm);
      Serial.print(F(" mm = "));
      Serial.print(steps);
      Serial.println(F(" steps"));
      return;
    }
  } else if (cmd.startsWith("Y+") || cmd.startsWith("Y-")) {
    long steps = 0;
    float distance_mm = 0;

    if (cmd == "Y+") distance_mm = 1.0;
    else if (cmd == "Y-") distance_mm = -1.0;
    else if (cmd == "Y++") distance_mm = 10.0;
    else if (cmd == "Y--") distance_mm = -10.0;
    else {
      float mm = cmd.substring(2).toFloat();
      if (cmd.startsWith("Y+")) distance_mm = mm;
      else if (cmd.startsWith("Y-")) distance_mm = -mm;
    }

    if (distance_mm != 0) {
      steps = (long)(distance_mm * STEPS_PER_MM_Y);
      if (INVERT_Y_DIR) steps = -steps;
      stepperY.setMaxSpeed(MOVE_SPEED_SPS);
      stepperY.move(steps);
      busy = true;
      Serial.print(F("[JOG Y] "));
      Serial.print(distance_mm);
      Serial.print(F(" mm = "));
      Serial.print(steps);
      Serial.println(F(" steps"));
      return;
    }
  } else if (cmd == "EN") {
    setEnable(true);
    Serial.println(F("[EN] ON"));
  } else if (cmd == "DIS") {
    setEnable(false);
    Serial.println(F("[EN] OFF"));
  } else if (cmd == "PINS") {
    Serial.print(F("[PINS] EN="));
    Serial.print(digitalRead(PIN_ENABLE));
    Serial.print(F(" X_LIMIT="));
    Serial.print(digitalRead(PIN_X_LIMIT));
    Serial.print(F(" PEN="));
    Serial.println(digitalRead(PIN_PEN));
  } else if (cmd == "HELP") {
    Serial.println(F("=== PLOTTER COMMANDS ==="));
    Serial.println(F("  X+50      - Move X by 50 mm right"));
    Serial.println(F("  X-30      - Move X by 30 mm left"));
    Serial.println(F("  Y+25      - Move Y by 25 mm forward"));
    Serial.println(F("  Y-10      - Move Y by 10 mm back"));
    Serial.println(F("  X+, X-    - Move X by 1 mm"));
    Serial.println(F("  X++, X--  - Move X by 10 mm"));
    Serial.println(F("  Y+, Y-    - Move Y by 1 mm"));
    Serial.println(F("  Y++, Y--  - Move Y by 10 mm"));
    Serial.println(F("  PEN       - Toggle pen up/down"));
    Serial.println(F("  HOME      - Go to home position"));
    Serial.println(F("  STOP      - Stop motors and clear queue"));
    Serial.println(F("  HALT      - Emergency stop"));
    Serial.println(F("  EN/DIS    - Enable/disable motors"));
    Serial.println(F("  PINS      - Read pin states"));
    Serial.println(F("  T or ?    - Status"));
    Serial.print(F("  Current calibration: X="));
    Serial.print(STEPS_PER_MM_X);
    Serial.print(F(" steps/mm, Y="));
    Serial.print(STEPS_PER_MM_Y);
    Serial.println(F(" steps/mm"));
  } else {
    Serial.print(F("[?] unknown: "));
    Serial.println(cmd);
  }
}

void handlePcByte(uint8_t b) {
  if (b == '\n' || b == '\r') {
    if (pcCmdBuf.length() > 0) {
      String cmd = pcCmdBuf;
      cmd.trim();
      cmd.toUpperCase();
      pcCmdBuf = "";
      execPcCommand(cmd);
    }
    return;
  }
  pcCmdBuf += (char)b;
  if (pcCmdBuf.length() > 30) pcCmdBuf = "";
  lastPcByte = millis();
}

// ===================== setup / loop ==================================
void setup() {
  pinMode(PIN_ENABLE, OUTPUT);
  setEnable(true);  // включаем драйверы

  pinMode(PIN_PEN, OUTPUT);
  penUp();

  pinMode(PIN_X_LIMIT, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);

  pinMode(PIN_ESTOP, INPUT_PULLUP);

  stepperX.setMaxSpeed(MOVE_SPEED_SPS);
  stepperX.setAcceleration(ACCEL_SPS2);
  stepperY.setMaxSpeed(MOVE_SPEED_SPS);
  stepperY.setAcceleration(ACCEL_SPS2);

  Serial.begin(USB_BAUD);
  headSerial.begin(HEAD_BAUD);

  Serial.println();
  Serial.println(F("======================================"));
  Serial.println(F("ACECUTTER_A30 CUSTOM MCU (Nano + CNC Shield)"));
  Serial.print(F("STEP X="));
  Serial.print(PIN_X_STEP);
  Serial.print(F(" DIR X="));
  Serial.print(PIN_X_DIR);
  Serial.print(F(" STEP Y="));
  Serial.print(PIN_Y_STEP);
  Serial.print(F(" DIR Y="));
  Serial.println(PIN_Y_DIR);
  Serial.print(F("EN="));
  Serial.print(PIN_ENABLE);
  Serial.print(F(" PEN="));
  Serial.print(PIN_PEN);
  Serial.print(F(" X_LIM="));
  Serial.println(PIN_X_LIMIT);
  Serial.print(F("HEAD UART rx="));
  Serial.print(PIN_HEAD_RX);
  Serial.print(F(" tx="));
  Serial.print(PIN_HEAD_TX);
  Serial.print(F(" @ "));
  Serial.println(HEAD_BAUD);
  Serial.println(F("Type HELP for diag commands"));
  Serial.println(F("======================================"));
}

void emergencyStop() {
  static bool wasStopped = false;
  if (!wasStopped) {
    Serial.println(F("!!! EMERGENCY STOP !!!"));
    Serial.println(F("Use 'EN' to re-enable"));
    stepperX.stop();
    stepperY.stop();
    stepperX.move(0);
    stepperY.move(0);
    qHead = qTail = 0;
    busy = false;
    drawActive = false;
    setEnable(false);  // отключаем питание моторов
    wasStopped = true;
  }
}

void loop() {
  // 0. Б - безопасность
  if (digitalRead(PIN_ESTOP) == LOW) {
    emergencyStop();
  }

  // 1. читаем байты от головы
  while (headSerial.available()) {
    uint8_t b = headSerial.read();
    parserFeed(b);
    Serial.write(b);  // эхо в USB лог
  }

  // 2. читаем USB Serial
  while (Serial.available()) {
    uint8_t b = Serial.read();
    handlePcByte(b);
  }
  // авто-выполнение если PIO Monitor не шлёт newline
  if (pcCmdBuf.length() > 0 && lastPcByte > 0
      && millis() - lastPcByte > 250) {
    handlePcByte('\n');
    lastPcByte = 0;
  }

  // 3. кинематика (КРИТИЧНО — вызывать как можно чаще)
  motionTick();

  // 4. парсер «закончил draw — пора сообщить голове»
  parserTick();

  // 5. LED heartbeat (только смена при изменении busy)
  static bool lastBusy = false;
  if (busy != lastBusy) {
    lastBusy = busy;
    digitalWrite(PIN_LED, busy);
  }
}
