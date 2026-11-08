/*
 * Plotter MCU firmware — Arduino Nano + CNC Shield V3 (GRBL pinout).
 * Version: 3.1 (калибровка + умный homing + протокол PC)
 */

#include <AccelStepper.h>
#include <SoftwareSerial.h>
#include <avr/wdt.h>
#include <EEPROM.h>

// ===================== КОНФИГУРАЦИЯ ====================================
constexpr bool INVERT_X_DIR = true;
constexpr bool INVERT_Y_DIR = true;

// Пины
constexpr uint8_t PIN_X_STEP = 5;
constexpr uint8_t PIN_X_DIR = 2;
constexpr uint8_t PIN_Y_STEP = 6;
constexpr uint8_t PIN_Y_DIR = 3;
constexpr uint8_t PIN_ENABLE = 8;
constexpr uint8_t PIN_ESTOP = 4;
constexpr uint8_t PIN_PEN = 11;
constexpr uint8_t PIN_LIMIT_LEFT = 9;
constexpr uint8_t PIN_LIMIT_RIGHT = 10;
constexpr uint8_t PIN_HEAD_RX = A5;
constexpr uint8_t PIN_HEAD_TX = A4;
constexpr uint8_t PIN_LED = 13;

constexpr bool PEN_ACTIVE_HIGH = true;
constexpr bool LIMIT_ACTIVE_LOW = false;

constexpr long HEAD_BAUD = 10000;
constexpr long USB_BAUD = 115200;

constexpr float STEPS_PER_MM_X = 36.36f;
constexpr float STEPS_PER_MM_Y = 32.00f;
constexpr int UNITS_PER_MM = 40;
constexpr float STEPS_PER_UNIT_X = STEPS_PER_MM_X / UNITS_PER_MM;
constexpr float STEPS_PER_UNIT_Y = STEPS_PER_MM_Y / UNITS_PER_MM;

constexpr long DEFAULT_CANVAS_X_MM = 280;
constexpr long DEFAULT_CANVAS_Y_MM = 400;

constexpr float SPEED_FAST = 800.0f;
constexpr float SPEED_SLOW = 300.0f;
constexpr float SPEED_CUT = 400.0f;
constexpr float SPEED_HOMING = 200.0f;
constexpr float SPEED_CALIBRATE = 250.0f;
constexpr float ACCEL_SPS2 = 2000.0f;

constexpr long SAFE_ZONE_MM = 30;
constexpr long STARTHOME_SEEK_MM = 250;
constexpr long STARTHOME_SEEK_STEPS = STARTHOME_SEEK_MM * STEPS_PER_MM_X;
constexpr long HOMING_PULL_OFF_MM = 10;
constexpr long HOMING_PULL_OFF_STEPS = HOMING_PULL_OFF_MM * STEPS_PER_MM_X;
constexpr unsigned long HOMING_TIMEOUT_MS = 30000;
constexpr unsigned long CAL_TIMEOUT_MS = 60000;

constexpr unsigned long PEN_DELAY_MS = 15;
constexpr uint8_t QUEUE_CAP = 96;

constexpr int EEPROM_MAGIC_ADDR = 0;
constexpr int EEPROM_CANVAS_X_ADDR = 4;
constexpr int EEPROM_CANVAS_Y_ADDR = 8;
constexpr uint32_t EEPROM_MAGIC = 0xCA11B007;

const char DEVICE_SERIAL[] PROGMEM = "002400143439470C35303835";
const char DEVICE_VERSION[] PROGMEM = "6";

// ===================== СТРУКТУРЫ ======================================
struct MotionCmd {
  bool pen_down;
  int16_t x_units;
  int16_t y_units;
};

class QueueManager {
  MotionCmd buffer[QUEUE_CAP];
  volatile uint8_t head = 0, tail = 0;
public:
  uint8_t size() { return (tail >= head) ? (tail - head) : (QUEUE_CAP - head + tail); }
  bool isEmpty() { return head == tail; }
  bool push(const MotionCmd& c) {
    uint8_t next = (tail + 1) % QUEUE_CAP;
    if (next == head) return false;
    buffer[tail] = c;
    tail = next;
    return true;
  }
  bool pop(MotionCmd& c) {
    if (head == tail) return false;
    c = buffer[head];
    head = (head + 1) % QUEUE_CAP;
    return true;
  }
  void clear() { head = tail = 0; }
};

// ===================== ГЛОБАЛЬНЫЕ ОБЪЕКТЫ ==============================
AccelStepper stepperX(AccelStepper::DRIVER, PIN_X_STEP, PIN_X_DIR);
AccelStepper stepperY(AccelStepper::DRIVER, PIN_Y_STEP, PIN_Y_DIR);
SoftwareSerial headSerial(PIN_HEAD_RX, PIN_HEAD_TX);
QueueManager cmdQueue;

long currentX = 0;
long currentY = 0;

bool penDown = false;
bool busy = false;
bool drawActive = false;
bool drawAck10_1Sent = false;
bool drawAck10_0Sent = true;
bool leftLimitActive = false;
bool rightLimitActive = false;
bool homingInProgress = false;
bool startHomeDone = false;
int pressureVal = 48;
int moveSpeedPct = 100;
int cutSpeedPct = 20;

long canvasXSteps = DEFAULT_CANVAS_X_MM * STEPS_PER_MM_X;
long canvasYSteps = DEFAULT_CANVAS_Y_MM * STEPS_PER_MM_Y;
long canvasXMM = DEFAULT_CANVAS_X_MM;
long canvasYMM = DEFAULT_CANVAS_Y_MM;
bool calibrated = false;

// ===================== EEPROM ==========================================
void saveCalibration() {
  EEPROM.put(EEPROM_MAGIC_ADDR, EEPROM_MAGIC);
  EEPROM.put(EEPROM_CANVAS_X_ADDR, canvasXSteps);
  EEPROM.put(EEPROM_CANVAS_Y_ADDR, canvasYSteps);
  Serial.print(F("CALIB SAVED: X="));
  Serial.print(canvasXMM);
  Serial.print(F("mm Y="));
  Serial.print(canvasYMM);
  Serial.println(F("mm"));
}

void loadCalibration() {
  uint32_t magic;
  EEPROM.get(EEPROM_MAGIC_ADDR, magic);
  
  if (magic == EEPROM_MAGIC) {
    EEPROM.get(EEPROM_CANVAS_X_ADDR, canvasXSteps);
    EEPROM.get(EEPROM_CANVAS_Y_ADDR, canvasYSteps);
    canvasXMM = canvasXSteps / STEPS_PER_MM_X;
    canvasYMM = canvasYSteps / STEPS_PER_MM_Y;
    calibrated = true;
    Serial.print(F("CALIB LOADED: X="));
    Serial.print(canvasXMM);
    Serial.print(F("mm Y="));
    Serial.print(canvasYMM);
    Serial.println(F("mm"));
  } else {
    Serial.println(F("CALIB: Using defaults"));
  }
}

// ===================== КОНЦЕВИКИ =====================================
inline bool readLeftLimit() {
  return digitalRead(PIN_LIMIT_LEFT) == (LIMIT_ACTIVE_LOW ? LOW : HIGH);
}

inline bool readRightLimit() {
  return digitalRead(PIN_LIMIT_RIGHT) == (LIMIT_ACTIVE_LOW ? LOW : HIGH);
}

inline bool readEStop() {
  return digitalRead(PIN_ESTOP) == LOW;
}

void updateLimitFlags() {
  leftLimitActive = readLeftLimit();
  rightLimitActive = readRightLimit();
}

// ===================== WDT ============================================
inline void petWDog() {
  wdt_reset();
}

// ===================== МОТОРЫ ========================================
void setEnable(bool en) {
  digitalWrite(PIN_ENABLE, en ? LOW : HIGH);
}

void penUp() {
  digitalWrite(PIN_PEN, PEN_ACTIVE_HIGH ? LOW : HIGH);
  penDown = false;
  delay(PEN_DELAY_MS);
}

void penDownNow() {
  digitalWrite(PIN_PEN, PEN_ACTIVE_HIGH ? HIGH : LOW);
  penDown = true;
  delay(PEN_DELAY_MS);
}

float getSmartSpeed(float baseSpeed) {
  long distToLeft = currentX;
  long distToRight = canvasXSteps - currentX;
  long safeSteps = SAFE_ZONE_MM * STEPS_PER_MM_X;
  
  if (distToLeft < safeSteps || distToRight < safeSteps) {
    if (baseSpeed > SPEED_SLOW) {
      return SPEED_SLOW;
    }
  }
  return baseSpeed;
}

void moveTo(long targetX, long targetY, float speed) {
  if (homingInProgress) {
    Serial.println(F("ERR: Homing in progress"));
    return;
  }
  
  // Проверка границ
  if (targetX < 0) targetX = 0;
  if (targetX > canvasXSteps) targetX = canvasXSteps;
  if (targetY < 0) targetY = 0;
  if (targetY > canvasYSteps) targetY = canvasYSteps;
  
  long dx = targetX - currentX;
  long dy = targetY - currentY;
  
  float smartSpeed = getSmartSpeed(speed);
  
  stepperX.setMaxSpeed(smartSpeed);
  stepperY.setMaxSpeed(smartSpeed);
  
  if (INVERT_X_DIR) dx = -dx;
  if (INVERT_Y_DIR) dy = -dy;
  
  stepperX.move(dx);
  stepperY.move(dy);
  
  currentX = targetX;
  currentY = targetY;
  
  Serial.print(F("Moving by X="));
  Serial.print(dx);
  Serial.print(F(" Y="));
  Serial.print(dy);
  Serial.print(F(" steps at "));
  Serial.print(smartSpeed);
  Serial.println(F(" sps"));
}

// ===================== STARTHOME ======================================
bool doStartHome() {
  Serial.println(F("STARTHOME: Starting..."));
  homingInProgress = true;
  penUp();
  setEnable(true);
  
  if (readLeftLimit()) {
    Serial.println(F("STARTHOME: At HOME, moving right..."));
    stepperX.setMaxSpeed(SPEED_SLOW);
    stepperX.move(-HOMING_PULL_OFF_STEPS * 3);
    
    unsigned long t0 = millis();
    while (stepperX.distanceToGo() != 0 && (millis() - t0 < 5000)) {
      stepperX.run();
      petWDog();
    }
    stepperX.stop();
    delay(200);
  }
  
  Serial.println(F("STARTHOME: Seeking HOME (250mm)..."));
  stepperX.setMaxSpeed(SPEED_HOMING);
  stepperX.move(STARTHOME_SEEK_STEPS);
  
  unsigned long startTime = millis();
  bool found = false;
  
  while (stepperX.distanceToGo() != 0) {
    stepperX.run();
    petWDog();
    
    if (readLeftLimit()) {
      stepperX.stop();
      while (stepperX.distanceToGo() != 0) { stepperX.run(); petWDog(); }
      Serial.println(F("STARTHOME: Found!"));
      found = true;
      break;
    }
    
    if (millis() - startTime > HOMING_TIMEOUT_MS) { stepperX.stop(); break; }
    if (readEStop()) { stepperX.stop(); homingInProgress = false; return false; }
  }
  
  if (!found) {
    Serial.println(F("STARTHOME: Not found!"));
    homingInProgress = false;
    return false;
  }
  
  delay(100);
  stepperX.setMaxSpeed(SPEED_SLOW);
  stepperX.move(-HOMING_PULL_OFF_STEPS);
  
  unsigned long t0 = millis();
  while (stepperX.distanceToGo() != 0 && (millis() - t0 < 5000)) {
    stepperX.run();
    petWDog();
  }
  
  stepperX.setCurrentPosition(0);
  stepperY.setCurrentPosition(0);
  currentX = HOMING_PULL_OFF_STEPS;
  currentY = 0;
  startHomeDone = true;
  homingInProgress = false;
  
  Serial.println(F("STARTHOME: OK"));
  return true;
}

// ===================== HOME ===========================================
bool doHomeX() {
  if (!startHomeDone) return doStartHome();
  if (homingInProgress) { Serial.println(F("HOME: Busy")); return false; }
  
  homingInProgress = true;
  Serial.println(F("HOME: Fast homing..."));
  penUp();
  setEnable(true);
  
  if (currentX < SAFE_ZONE_MM * STEPS_PER_MM_X * 2) {
    Serial.println(F("HOME: Backing off..."));
    stepperX.setMaxSpeed(SPEED_SLOW);
    stepperX.move(-SAFE_ZONE_MM * STEPS_PER_MM_X * 2);
    
    unsigned long t0 = millis();
    while (stepperX.distanceToGo() != 0 && (millis() - t0 < 5000)) {
      stepperX.run();
      petWDog();
    }
    delay(200);
    currentX = SAFE_ZONE_MM * STEPS_PER_MM_X * 2;
  }
  
  long fastDist = currentX - SAFE_ZONE_MM * STEPS_PER_MM_X;
  if (fastDist > 0) {
    Serial.print(F("HOME: Fast "));
    Serial.print(fastDist / STEPS_PER_MM_X);
    Serial.println(F("mm"));
    
    stepperX.setMaxSpeed(SPEED_FAST);
    stepperX.move(-fastDist);
    
    unsigned long t0 = millis();
    while (stepperX.distanceToGo() != 0 && (millis() - t0 < HOMING_TIMEOUT_MS)) {
      stepperX.run();
      petWDog();
      if (readLeftLimit()) { stepperX.stop(); break; }
    }
  }
  
  Serial.println(F("HOME: Slow approach..."));
  stepperX.setMaxSpeed(SPEED_HOMING);
  stepperX.move(SAFE_ZONE_MM * STEPS_PER_MM_X * 3);
  
  unsigned long startTime = millis();
  bool found = false;
  
  while (stepperX.distanceToGo() != 0) {
    stepperX.run();
    petWDog();
    
    if (readLeftLimit()) {
      stepperX.stop();
      while (stepperX.distanceToGo() != 0) { stepperX.run(); petWDog(); }
      found = true;
      break;
    }
    
    if (millis() - startTime > HOMING_TIMEOUT_MS) { stepperX.stop(); break; }
    if (readEStop()) { stepperX.stop(); homingInProgress = false; return false; }
  }
  
  if (!found) { homingInProgress = false; return false; }
  
  delay(100);
  stepperX.setMaxSpeed(SPEED_SLOW);
  stepperX.move(-HOMING_PULL_OFF_STEPS);
  
  unsigned long t0 = millis();
  while (stepperX.distanceToGo() != 0 && (millis() - t0 < 5000)) {
    stepperX.run();
    petWDog();
  }
  
  stepperX.setCurrentPosition(0);
  stepperY.setCurrentPosition(0);
  currentX = HOMING_PULL_OFF_STEPS;
  currentY = 0;
  homingInProgress = false;
  
  Serial.println(F("HOME: OK"));
  return true;
}

// ===================== КАЛИБРОВКА =====================================
bool calibrateCanvas() {
  Serial.println(F("CALIB: Starting..."));
  
  if (!doHomeX()) {
    Serial.println(F("CALIB: HOME failed!"));
    return false;
  }
  
  delay(500);
  homingInProgress = true;
  
  // Едем вправо до LIMIT медленно
  Serial.println(F("CALIB: Measuring X..."));
  stepperX.setMaxSpeed(SPEED_CALIBRATE);
  stepperX.move(-200000);  // Большой запас вправо
  
  unsigned long startTime = millis();
  bool found = false;
  
  while (stepperX.distanceToGo() != 0) {
    stepperX.run();
    petWDog();
    
    if (readRightLimit()) {
      stepperX.stop();
      while (stepperX.distanceToGo() != 0) { stepperX.run(); petWDog(); }
      
      long rawSteps = abs(stepperX.currentPosition());
      canvasXSteps = rawSteps + HOMING_PULL_OFF_STEPS;
      canvasXMM = canvasXSteps / STEPS_PER_MM_X;
      calibrated = true;
      found = true;
      
      Serial.print(F("CALIB: X="));
      Serial.print(canvasXMM);
      Serial.print(F("mm ("));
      Serial.print(canvasXSteps);
      Serial.println(F(" steps)"));
      break;
    }
    
    if (millis() - startTime > CAL_TIMEOUT_MS) { stepperX.stop(); break; }
    if (readEStop()) { stepperX.stop(); homingInProgress = false; return false; }
  }
  
  if (!found) {
    Serial.println(F("CALIB: RIGHT limit not found!"));
    homingInProgress = false;
    return false;
  }
  
  // Сохраняем
  saveCalibration();
  
  // Возвращаемся к HOME
  Serial.println(F("CALIB: Returning to HOME..."));
  stepperX.setMaxSpeed(SPEED_FAST);
  stepperX.move(canvasXSteps);
  
  startTime = millis();
  while (stepperX.distanceToGo() != 0 && (millis() - startTime < HOMING_TIMEOUT_MS)) {
    stepperX.run();
    petWDog();
    if (readLeftLimit()) { stepperX.stop(); break; }
  }
  
  delay(100);
  stepperX.setMaxSpeed(SPEED_SLOW);
  stepperX.move(-HOMING_PULL_OFF_STEPS);
  
  startTime = millis();
  while (stepperX.distanceToGo() != 0 && (millis() - startTime < 5000)) {
    stepperX.run();
    petWDog();
  }
  
  stepperX.setCurrentPosition(0);
  stepperY.setCurrentPosition(0);
  currentX = HOMING_PULL_OFF_STEPS;
  currentY = 0;
  homingInProgress = false;
  
  Serial.println(F("CALIB: OK"));
  return true;
}

// ===================== КОМАНДЫ ========================================
void handlePCCommand(const String& cmd) {
  if (cmd == "STARTHOME") {
    doStartHome();
  }
  else if (cmd == "HOME") {
    doHomeX();
  }
  else if (cmd == "CALIBRATE") {
    calibrateCanvas();
  }
  else if (cmd == "STATUS") {
    Serial.print(F("STATUS X="));
    Serial.print(currentX / STEPS_PER_MM_X);
    Serial.print(F(" Y="));
    Serial.print(currentY / STEPS_PER_MM_Y);
    Serial.print(F(" Pen="));
    Serial.print(penDown ? "DOWN" : "UP");
    Serial.print(F(" Left="));
    Serial.print(leftLimitActive);
    Serial.print(F(" Right="));
    Serial.print(rightLimitActive);
    Serial.print(F(" Calibrated="));
    Serial.print(calibrated ? "YES" : "NO");
    Serial.print(F(" CanvasX="));
    Serial.print(canvasXMM);
    Serial.print(F(" CanvasY="));
    Serial.println(canvasYMM);
  }
  else if (cmd.startsWith("MOVE ")) {
    // Парсим MOVE X Y SPEED
    String params = cmd.substring(5);  // Убираем "MOVE "
    params.trim();
    
    // Разделяем по пробелам
    int space1 = params.indexOf(' ');
    int space2 = params.indexOf(' ', space1 + 1);
    
    if (space1 > 0 && space2 > space1) {
      float x = params.substring(0, space1).toFloat();
      float y = params.substring(space1 + 1, space2).toFloat();
      float speed = params.substring(space2 + 1).toFloat();
      
      if (speed == 0) speed = 500;  // Скорость по умолчанию
      
      long targetX = x * STEPS_PER_MM_X;
      long targetY = y * STEPS_PER_MM_Y;
      
      Serial.print(F("MOVE to X="));
      Serial.print(x);
      Serial.print(F("mm Y="));
      Serial.print(y);
      Serial.print(F("mm Speed="));
      Serial.println(speed);
      
      moveTo(targetX, targetY, speed);
      busy = true;
    } else {
      Serial.println(F("ERR: Bad MOVE format. Use: MOVE X Y SPEED"));
    }
  }
  else if (cmd == "PEN UP") {
    penUp();
    Serial.println(F("OK PEN UP"));
  }
  else if (cmd == "PEN DOWN") {
    penDownNow();
    Serial.println(F("OK PEN DOWN"));
  }
  else if (cmd == "STOP") {
    stepperX.stop();
    stepperY.stop();
    busy = false;
    homingInProgress = false;
    Serial.println(F("OK STOP"));
  }
  else if (cmd == "RESET") {
    homingInProgress = false;
    setEnable(true);
    Serial.println(F("OK RESET"));
  }
  else if (cmd.startsWith("SET ")) {
    // Парсим SET X Y
    String params = cmd.substring(4);
    params.trim();
    
    int space = params.indexOf(' ');
    if (space > 0) {
      float x = params.substring(0, space).toFloat();
      float y = params.substring(space + 1).toFloat();
      
      canvasXMM = x;
      canvasYMM = y;
      canvasXSteps = x * STEPS_PER_MM_X;
      canvasYSteps = y * STEPS_PER_MM_Y;
      calibrated = true;
      saveCalibration();
      Serial.println(F("OK SET"));
    }
  }
  else {
    Serial.print(F("UNKNOWN: "));
    Serial.println(cmd);
  }
}

// ===================== SETUP ==========================================
void setup() {
  wdt_enable(WDTO_8S);
  
  pinMode(PIN_ENABLE, OUTPUT);
  setEnable(true);
  pinMode(PIN_PEN, OUTPUT);
  penUp();
  pinMode(PIN_LIMIT_LEFT, INPUT_PULLUP);
  pinMode(PIN_LIMIT_RIGHT, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_ESTOP, INPUT_PULLUP);
  
  stepperX.setMaxSpeed(SPEED_FAST);
  stepperX.setAcceleration(ACCEL_SPS2);
  stepperY.setMaxSpeed(SPEED_FAST);
  stepperY.setAcceleration(ACCEL_SPS2);
  
  Serial.begin(USB_BAUD);
  headSerial.begin(HEAD_BAUD);
  
  Serial.println(F("\n=== ACECUTTER_A30 v3.1 ==="));
  Serial.println(F("Commands: STARTHOME, HOME, CALIBRATE"));
  Serial.println(F("          MOVE X Y S, PEN UP/DOWN"));
  Serial.println(F("          STATUS, STOP, RESET"));
  
  loadCalibration();
  
  Serial.println(F("Auto STARTHOME..."));
  doStartHome();
  
  Serial.println(F("READY"));
}

// ===================== LOOP ===========================================
void loop() {
  petWDog();
  
  if (readEStop()) {
    Serial.println(F("E-STOP!"));
    stepperX.stop();
    stepperY.stop();
    busy = false;
    homingInProgress = false;
    if (penDown) penUp();
    setEnable(false);
    while (readEStop()) { petWDog(); delay(10); }
  }
  
  // Старый протокол (голова)
  while (headSerial.available()) {
    headSerial.read();
    petWDog();
  }
  
  // Новый протокол (PC)
  static String pcBuf = "";
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (pcBuf.length() > 0) {
        pcBuf.trim();
        pcBuf.toUpperCase();
        handlePCCommand(pcBuf);
        pcBuf = "";
      }
    } else {
      pcBuf += c;
      if (pcBuf.length() > 60) pcBuf = "";
    }
  }
  
  // Движение
  if (busy) {
    stepperX.run();
    stepperY.run();
    
    if (stepperX.distanceToGo() == 0 && stepperY.distanceToGo() == 0) {
      busy = false;
      Serial.println(F("DONE"));
    }
    
    updateLimitFlags();
    if (leftLimitActive || rightLimitActive) {
      stepperX.stop();
      stepperY.stop();
      busy = false;
      Serial.println(F("LIMIT"));
    }
  }
  
  digitalWrite(PIN_LED, busy || homingInProgress);
  petWDog();
}