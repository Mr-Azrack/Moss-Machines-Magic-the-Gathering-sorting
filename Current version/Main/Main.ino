#include <LiquidCrystal_I2C.h>
#include <Stepper.h>
#include <Wire.h>
#include "Adafruit_VL6180X.h"

// ============================================================
// ERROR CODES — mirrors error_codes.py on the PC side
// Format: <Error,EXXX,short description>
// ============================================================
#define EC_TOF_NOT_FOUND      "E501"
#define EC_TOF_SYS_ERR        "E502"
#define EC_TOF_ECE            "E503"
#define EC_TOF_NO_CONVERGE    "E504"
#define EC_PICKUP_RETRY       "E505"
#define EC_OVERFLOW_FULL      "E506"
#define EC_CMD_NOT_STARTED    "E507"
#define EC_CMD_STARTED        "E508"
#define EC_NOT_AT_HOME        "E509"
#define EC_HOME_X_FAIL        "E510"
#define EC_HOME_Y_FAIL        "E511"
#define EC_HOME_Z_FAIL        "E512"
#define EC_BUF_OVERFLOW       "E513"
#define EC_UNKNOWN_CMD        "E514"
#define EC_LOADING_POSITION   "E515"
#define EC_Z_TRAVEL_HEIGHT     "E516"
#define EC_LOADING_SEEK_FAIL    "E517"
#define FIRMWARE_ID             "MOSS-ENDSTOP-PARK-5"

#define REPORT_ERROR(code, msg) \
  do { \
    Serial.println("<Error," + String(code) + "," + String(msg) + ">"); \
    lcd.setCursor(0, 1); lcd.print(String(code) + " " + String(msg).substring(0, 11)); \
  } while(0)

enum Pins {
    Zmin = 18, Xmin = 3, Ymin = 14, Xmax = 2, Ymax = 15, Zmax = 19,
    Lights = 8, Vacuum1 = 9, Vacuum2 = 10,
    Xenable = 38, Yenable = 56, Zenable = 62, E0enable = 24, E1enable = 30,
    Xstep = 54, Ystep = 60, Zstep = 46, E0step = 26, E1step = 36,
    Xdir = 55, Ydir = 61, Zdir = 48, E0dir = 28, E1dir = 34
};

Adafruit_VL6180X vl = Adafruit_VL6180X();
LiquidCrystal_I2C lcd(0x27, 16, 2);

boolean newData = false, readInProgress = false, newDataFromPC = false;
boolean atHomePosition = false;
boolean machineStarted = false;
boolean loadingParked = false;           // Xmax/Ymin switches asserted, Z fully raised
boolean loadingReturnReady = false;      // Returned to calibrated pickup/home
boolean loadingMotionActive = false;     // STOP responsive during parking
boolean loadingRecoveryRequired = false; // interrupted move -> empty hopper and HOME
boolean abortRequested = false;
boolean vacuumCarryActive = false;
unsigned long vacuumCarryCycleStart = 0;
byte bytesRecvd = 0, PickupRetry;
const byte numChars = 64, buffSize = 40;
const char startMarker = '<', endMarker = '>';
char inputBuffer[buffSize], messageFromPC[buffSize] = { 0 }, receivedChars[numChars];

float timeoutcount = 0;
const float timeout = 10;

const char* MatchingValues[] = {"RejectCard", "tray1", "tray7", "tray14", "tray18","tray25", "tray26", "tray27", "tray28", "tray29", "tray30", "tray31", "tray32"};
const int loopStart[] = {33, 1, 7, 14, 18, 25, 26, 27, 28, 29, 30, 31, 32};
const int loopEnd[] = {33, 6, 13, 17, 24, 25, 26, 27, 28, 29, 30, 31, 32};
boolean match = 0;
const int xOffsets[6] = {-3, -2, -1, 1, 2, 3};
const int yOffsets[4] = {-2, -1, 1, 2};
const short X[6][5]={{31,27,21,28,32},{23,11,9,12,24},{17,5,1,6,18},{19,7,2,8,20},{25,13,10,14,26},{33,29,22,30,34}};
const short Y[4][7]={{32,24,18,16,20,26,34},{28,12,6,4,8,14,30},{27,11,5,3,7,13,29},{31,23,17,15,19,25,33}};
short CountArray[35], upcount;
String AssignedTrayValue[35], Tempval1;
uint8_t range[6];

byte X_ENDSTOP_MIN, Y_ENDSTOP_MIN, Z_ENDSTOP_MIN, X_ENDSTOP_MAX, Y_ENDSTOP_MAX, Z_ENDSTOP_MAX;
long zPositionSteps = 0;
const long Z_SOFT_MAX_STEPS = 95935L;
const long Z_MANUAL_CAL_MAX_STEPS = 103000L;  // Manual calibration only; auto sorting remains constrained above.
const short PICKUP_CONTACT_MM = 5;
const long PICKUP_COMPRESSION_STEPS = 2805L;  // ~3.0 mm extra cup compression at Zcal 935.
const long CARRY_CLEARANCE_STEPS = 6000L;     // Return from the sensing height to the pickup-cycle Z=0 travel height.
const byte MAX_PICKUP_ATTEMPTS = 3;
const unsigned short PICKUP_VACUUM_PULSE_MS = 1000;
const unsigned short VACUUM_CARRY_PULSE_ON_MS = 1000;
const unsigned short VACUUM_CARRY_PULSE_GAP_MS = 1500;
const unsigned short VACUUM_CARRY_CYCLE_MS = VACUUM_CARRY_PULSE_ON_MS + VACUUM_CARRY_PULSE_GAP_MS;
const unsigned short DROP_SETTLE_MS = 2500;

short initial_pickup_distance = 6000;
short Xcal = 350, Ycal = 475, Zcal = 935;
short speed = 500, zspeed = 28, zespeed = 36;
short pickup_threshold = 40, release_threshold = 40;
short HCC = 10, YCourseCorrection = 1, XCourseCorrection = 0;
// Pickup travel home is 3000 Z steps below the Zmin switch.
const long PICKUP_Z_FROM_LIMIT_STEPS = 3000L;
const unsigned long LOADING_Z_SEEK_TIMEOUT_MS = 60000UL;
const unsigned long LOADING_XY_SEEK_TIMEOUT_MS = 15000UL;
const byte ENDSTOP_TRIGGERED = LOW;

void setup() {
  byte pins[] = { Xstep, Ystep, Zstep, E0step, E1step, Xdir, Ydir, Zdir, E0dir, E1dir, Xenable, Yenable, Zenable, E0enable, E1enable, Vacuum1, Vacuum2, Lights};
  for (byte pin : pins) { pinMode(pin, OUTPUT); }
  // Endstops are active LOW (pressed = 0). Floating INPUT pins can
  // falsely read 0 and make LOAD CARDS claim it reached the switches
  // without moving. Pull-ups make an open switch reliably read HIGH.
  byte endstopPins[] = {Xmin, Xmax, Ymin, Ymax, Zmin, Zmax};
  for (byte pin : endstopPins) { pinMode(pin, INPUT_PULLUP); }

  digitalWrite(Xenable, LOW);
  digitalWrite(Yenable, LOW);
  digitalWrite(Zenable, LOW);
  digitalWrite(E0enable, LOW);
  digitalWrite(E1enable, LOW);
  digitalWrite(Vacuum1, LOW);
  digitalWrite(Vacuum2, LOW);
  digitalWrite(Lights, HIGH);

  Serial.begin(9600);
  Serial.setTimeout(100);
  digitalWrite(13, HIGH);
  while (!Serial) { delay(10); }
  Serial.println("Adafruit VL6180x test!");
  while (!vl.begin()) {
    REPORT_ERROR(EC_TOF_NOT_FOUND, "No ToF sensor");
    PrintLCD("E501 No ToF", "Check I2C wiring");
    delay(1000);
  }
  Serial.println("Sensor found!");
  PrintLCD("ToF sensor", "found");
  delay(100);
  Serial.println("<Arduino is ready>");
}

void loop() {
  uint8_t status = vl.readRangeStatus();
  if (AssignedTrayValue[34] != "OverflowTray") {AssignedTrayValue[34] = "OverflowTray";}
  if (AssignedTrayValue[33] != "RejectCard") {AssignedTrayValue[33] = "RejectCard";}

  if (status == VL6180X_ERROR_NONE) {
    ReadRange(1);
    Serial.print("Range: ");
    Serial.println((range[0] + range[1]) / 2);
  } else {
    PrintLCD("ToF: No range", " ");
  }

  switch (status) {
    case VL6180X_ERROR_SYSERR_1 ... VL6180X_ERROR_SYSERR_5:
      REPORT_ERROR(EC_TOF_SYS_ERR, "System error"); break;
    case VL6180X_ERROR_ECEFAIL:
      REPORT_ERROR(EC_TOF_ECE, "ECE failure"); break;
    case VL6180X_ERROR_NOCONVERGE:
      REPORT_ERROR(EC_TOF_NO_CONVERGE, "No convergence"); break;
    case VL6180X_ERROR_RANGEIGNORE:
      Serial.println("<Info,E504,Ignoring range>"); break;
    case VL6180X_ERROR_SNR:
      REPORT_ERROR(EC_TOF_SYS_ERR, "Signal/Noise err"); break;
    case VL6180X_ERROR_RAWUFLOW:
      REPORT_ERROR(EC_TOF_SYS_ERR, "Raw underflow"); break;
    case VL6180X_ERROR_RAWOFLOW:
      REPORT_ERROR(EC_TOF_SYS_ERR, "Raw overflow"); break;
    case VL6180X_ERROR_RANGEUFLOW:
      REPORT_ERROR(EC_TOF_SYS_ERR, "Range underflow"); break;
    case VL6180X_ERROR_RANGEOFLOW:
      REPORT_ERROR(EC_TOF_SYS_ERR, "Range overflow"); break;
    default: break;
  }

  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  ReadEndstops();
  PrintLCD("Ready", " ");

  Tempval1 = Serial.readString();
  delay(10);
  Tempval1.trim();
  if (Tempval1 != "") {
    PrintLCD("Received: ", Tempval1);
    ReadRange(1);
    DetermineAction();
    Serial.println("<Arduino is ready>");
    timeoutcount = 0;
  }

  Tempval1 = "";
  messageFromPC == "";
  bytesRecvd == 0;
  newData = false;
  readInProgress = false;
  newDataFromPC = false;
}

void getDataFromPC() {
  if (Serial.available() > 0) {
    char x = Serial.read();
    if (x == endMarker) {
      readInProgress = false;
      newDataFromPC = true;
      inputBuffer[bytesRecvd] = 0;
      parseData();
    }
    if (readInProgress) {
      inputBuffer[bytesRecvd] = x;
      bytesRecvd++;
      if (bytesRecvd == buffSize) {
        bytesRecvd = buffSize - 1;
        REPORT_ERROR(EC_BUF_OVERFLOW, "Cmd truncated");
      }
    }
    if (x == startMarker) {
      bytesRecvd = 0;
      readInProgress = true;
    }
  }
}

void parseData() {
  char* strtokIndx;
  strtokIndx = strtok(inputBuffer, ",");
  strcpy(messageFromPC, strtokIndx);
  strtokIndx = strtok(NULL, ",");
  strtokIndx = strtok(NULL, ",");
}

void startVacuumCarryPulse() {
  vacuumCarryActive = true;
  vacuumCarryCycleStart = millis();
  digitalWrite(Vacuum1, HIGH);
}

void stopVacuumCarryPulse() {
  vacuumCarryActive = false;
  digitalWrite(Vacuum1, LOW);
}

void updateVacuumCarryPulse() {
  if (!vacuumCarryActive) return;

  unsigned long phase = (millis() - vacuumCarryCycleStart) % VACUUM_CARRY_CYCLE_MS;
  digitalWrite(Vacuum1, (phase < VACUUM_CARRY_PULSE_ON_MS) ? HIGH : LOW);
}

boolean checkEmergencyStop() {
  if (abortRequested) return true;
  if ((!machineStarted && !loadingMotionActive) || Serial.available() <= 0) return false;

  String incoming = Serial.readString();
  incoming.trim();
  if (incoming == "StopMachine") {
    abortRequested = true;
    machineStarted = false;
    if (loadingMotionActive) loadingRecoveryRequired = true;
    stopVacuumCarryPulse();
    digitalWrite(Vacuum2, LOW);
    Serial.println("<OK,MachineStopped>");
    PrintLCD("STOPPED", "Cycle aborted");
    return true;
  }

  return false;
}

boolean delayWithStop(unsigned long waitMs) {
  unsigned long startedAt = millis();
  while (millis() - startedAt < waitMs) {
    updateVacuumCarryPulse();
    if (abortRequested) return false;
    if (machineStarted && checkEmergencyStop()) return false;
    delay(10);
  }
  return true;
}

// Release the card over the selected tray at full travel height (Z=0).
// Pickup already restored Z to 0 BEFORE traveling in X/Y. Never make an
// extra descent/ascent at the destination; preserve the established vacuum
// shutoff, short driver pulse, and card-settling timings.
boolean releaseCardAtTravelHeight() {
  if (abortRequested || !machineStarted) return false;
  if (zPositionSteps != 0) {
    StopMachine(); // Fail closed: XY/Z alignment is not known to be safe.
    REPORT_ERROR(EC_Z_TRAVEL_HEIGHT, "Z not raised");
    return false;
  }

  // Vacuum2 remains unused by the automatic sort cycle.
  // Keep all stepper drivers energized while the vacuum releases. Briefly
  // disabling the gantry at an outer tray can let an axis lose position.
  stopVacuumCarryPulse();
  if (!delayWithStop(100)) return false;

  return delayWithStop(DROP_SETTLE_MS);
}

boolean pick(long steps) {
  PickupRetry = 0;
  while (PickupRetry < MAX_PICKUP_ATTEMPTS) {
    Move1(0, steps, zspeed);
    if (abortRequested) return false;

    // Establish the seal, then use a low-duty keep-alive pulse while the
    // card is being lifted and carried. This compensates for a slow leak
    // without requiring Vacuum1 to run continuously for the whole cycle.
    digitalWrite(Vacuum1, HIGH);
    if (!delayWithStop(PICKUP_VACUUM_PULSE_MS)) {
      digitalWrite(Vacuum1, LOW);
      return false;
    }
    startVacuumCarryPulse();

    Move1(1, steps, zespeed);
    if (abortRequested) {
      stopVacuumCarryPulse();
      return false;
    }

    ReadRange(3);
    if (((range[2] + range[3]) / 2) <= pickup_threshold) {
      return true;
    }

    stopVacuumCarryPulse();
    PickupRetry++;
    if (PickupRetry < MAX_PICKUP_ATTEMPTS) {
      if (!delayWithStop(150)) return false;
    }
  }

  stopVacuumCarryPulse();
  digitalWrite(Vacuum2, LOW);
  machineStarted = false;
  REPORT_ERROR(EC_PICKUP_RETRY, "3 retries failed");
  PrintLCD("E505 Pickup", "Cycle stopped");
  return false;
}

boolean moveFromEndstopsToPickupXY() {
  // Precision/recovery move: return one axis at a time so the faster travel
  // timing does not load both XY pairs simultaneously while establishing the
  // calibrated pickup origin.
  Move4(1, 0, Xcal * 3 + 55, 0);
  if (abortRequested) return false;
  Move4(1, 0, 0, Ycal * 2 + 38);
  return !abortRequested;
}

void homingFailure(const char* reason, const char* code) {
  loadingMotionActive = false;
  loadingParked = false;
  loadingReturnReady = false;
  loadingRecoveryRequired = true;
  atHomePosition = false;
  stopVacuumCarryPulse();
  digitalWrite(Vacuum2, LOW);
  REPORT_ERROR(code, reason);
  PrintLCD("HOME FAILED", reason);
}

void Homemachine() {
  abortRequested = false;  // A deliberate HomeButton can follow StopMachine.
  loadingMotionActive = true;
  loadingReturnReady = false;
  atHomePosition = false;
  PrintLCD("Calibrating", " ");
  ReadEndstops();

  if (Z_ENDSTOP_MIN == 1) {
    unsigned long t0 = millis();
    while (Z_ENDSTOP_MIN == 1) {
      Move1(1, 3, zespeed);
      Z_ENDSTOP_MIN = digitalRead(Zmin);
      if (abortRequested) { homingFailure("Z interrupted", EC_LOADING_SEEK_FAIL); return; }
      if (millis() - t0 > 60000UL) {
        homingFailure("Z endstop", EC_HOME_Z_FAIL); return;
      }
    }
    delay(200);
  }

  if (Y_ENDSTOP_MIN == 1) {
    Move4(1, 0, 0, 50);
    if (abortRequested) { homingFailure("Y interrupted", EC_LOADING_SEEK_FAIL); return; }
    unsigned long t1 = millis();
    while (Y_ENDSTOP_MIN == 1) {
      Move4(0, 1, 0, 5);
      Y_ENDSTOP_MIN = digitalRead(Ymin);
      if (abortRequested) { homingFailure("Y interrupted", EC_LOADING_SEEK_FAIL); return; }
      if (millis() - t1 > 10000UL) {
        homingFailure("Y endstop", EC_HOME_Y_FAIL); return;
      }
    }
    delay(200);
  }

  if (X_ENDSTOP_MAX == 1) {
    Move4(1, 0, 50, 0);
    if (abortRequested) { homingFailure("X interrupted", EC_LOADING_SEEK_FAIL); return; }
    unsigned long t2 = millis();
    while (X_ENDSTOP_MAX == 1) {
      Move4(0, 1, 3, 0);
      X_ENDSTOP_MAX = digitalRead(Xmax);
      if (abortRequested) { homingFailure("X interrupted", EC_LOADING_SEEK_FAIL); return; }
      if (millis() - t2 > 10000UL) {
        homingFailure("X endstop", EC_HOME_X_FAIL); return;
      }
    }
    delay(200);
  }

  Move1(0, PICKUP_Z_FROM_LIMIT_STEPS, zspeed);
  if (abortRequested) { homingFailure("Z interrupted", EC_LOADING_SEEK_FAIL); return; }
  delay(200);
  if (!moveFromEndstopsToPickupXY()) {
    homingFailure("XY interrupted", EC_LOADING_SEEK_FAIL); return;
  }
  delay(200);
  zPositionSteps = 0;
  loadingMotionActive = false;
  abortRequested = false;
  atHomePosition = true;
  loadingRecoveryRequired = false;
  loadingParked = false;
  Serial.println("<OK,MachineHomed>");
}

// Dedicated load-park routine: never call Tray, pick, or any vacuum-on action.
void loadingSeekFailure(const char* reason) {
  loadingMotionActive = false;
  loadingParked = false;
  loadingReturnReady = false;
  loadingRecoveryRequired = true;
  atHomePosition = false;
  stopVacuumCarryPulse();
  digitalWrite(Vacuum2, LOW);
  REPORT_ERROR(EC_LOADING_SEEK_FAIL, reason);
  PrintLCD("LOAD FAILED", reason);
}

void ParkForLoading() {
  if (machineStarted || loadingMotionActive) {
    REPORT_ERROR(EC_CMD_STARTED, "Stop first"); return;
  }
  if (loadingParked) { Serial.println("<OK,LoadingParked>"); return; }
  if (loadingRecoveryRequired || !atHomePosition || zPositionSteps != 0) {
    REPORT_ERROR(EC_LOADING_POSITION, "Empty hopper/Home"); return;
  }
  // At the normal pickup coordinate the head is offset AWAY from all three
  // switches. If any already reads pressed, report a wiring/position fault
  // instead of falsely claiming a park completed with no motor movement.
  ReadEndstops();
  if (Z_ENDSTOP_MIN == ENDSTOP_TRIGGERED ||
      X_ENDSTOP_MAX == ENDSTOP_TRIGGERED ||
      Y_ENDSTOP_MIN == ENDSTOP_TRIGGERED) {
    loadingSeekFailure("Switch active at home");
    return;
  }
  abortRequested = false;
  loadingMotionActive = true;
  stopVacuumCarryPulse();
  digitalWrite(Vacuum2, LOW);
  MotorsOnOff(0);
  atHomePosition = false;
  loadingReturnReady = false;

  // Raise to the physical Zmin stop. Normal Z=0 travel height is below it.
  unsigned long zStart = millis();
  long zMoved = 0;
  while (digitalRead(Zmin) != ENDSTOP_TRIGGERED) {
    if (zMoved >= PICKUP_Z_FROM_LIMIT_STEPS + 750L) {
      loadingSeekFailure("Z limit not reached"); return;
    }
    Move1(1, 3, zespeed);  // Z UP ONLY
    zMoved += 3;
    if (abortRequested) { loadingSeekFailure("Z interrupted"); return; }
    if (millis() - zStart > LOADING_Z_SEEK_TIMEOUT_MS) {
      loadingSeekFailure("Z timeout"); return;
    }
  }
  zPositionSteps = -PICKUP_Z_FROM_LIMIT_STEPS;

  // Same seek directions used by Homemachine(), but no backoff, Z descent,
  // pickup, tray selection, vacuum action, or return move during parking.
  unsigned long yStart = millis();
  long yMoved = 0;
  while (digitalRead(Ymin) != ENDSTOP_TRIGGERED) {
    if (yMoved >= (long)Ycal * 2L + 38L + 750L) {
      loadingSeekFailure("Y limit not reached"); return;
    }
    Move4(0, 1, 0, 5);
    yMoved += 5;
    if (abortRequested) { loadingSeekFailure("Y interrupted"); return; }
    if (millis() - yStart > LOADING_XY_SEEK_TIMEOUT_MS) {
      loadingSeekFailure("Y timeout"); return;
    }
  }
  unsigned long xStart = millis();
  long xMoved = 0;
  while (digitalRead(Xmax) != ENDSTOP_TRIGGERED) {
    if (xMoved >= (long)Xcal * 3L + 55L + 750L) {
      loadingSeekFailure("X limit not reached"); return;
    }
    Move4(0, 1, 3, 0);
    xMoved += 3;
    if (abortRequested) { loadingSeekFailure("X interrupted"); return; }
    if (millis() - xStart > LOADING_XY_SEEK_TIMEOUT_MS) {
      loadingSeekFailure("X timeout"); return;
    }
  }

  ReadEndstops();
  if (Z_ENDSTOP_MIN != ENDSTOP_TRIGGERED ||
      X_ENDSTOP_MAX != ENDSTOP_TRIGGERED ||
      Y_ENDSTOP_MIN != ENDSTOP_TRIGGERED) {
    loadingSeekFailure("Limits not set"); return;
  }

  loadingMotionActive = false;
  loadingParked = true;
  Serial.println("<OK,LoadingParked>");
  PrintLCD("LOAD CARDS", "XY limits/Z up");
}

void ReturnFromLoading() {
  if (machineStarted || loadingMotionActive) {
    REPORT_ERROR(EC_CMD_STARTED, "Stop first"); return;
  }
  if (loadingRecoveryRequired || !loadingParked) {
    REPORT_ERROR(EC_LOADING_POSITION, "Not parked"); return;
  }
  ReadEndstops();
  if (Z_ENDSTOP_MIN != ENDSTOP_TRIGGERED ||
      X_ENDSTOP_MAX != ENDSTOP_TRIGGERED ||
      Y_ENDSTOP_MIN != ENDSTOP_TRIGGERED) {
    loadingSeekFailure("Park lost"); return;
  }

  abortRequested = false;
  loadingMotionActive = true;
  stopVacuumCarryPulse();
  digitalWrite(Vacuum2, LOW);
  MotorsOnOff(0);

  // From physical Xmax/Ymin, use precisely the offset of Homemachine().
  // Move in XY at full Z clearance; lower to pickup travel height last.
  if (!moveFromEndstopsToPickupXY()) {
    loadingSeekFailure("XY interrupted"); return;
  }
  Move1(0, PICKUP_Z_FROM_LIMIT_STEPS, zspeed);
  if (abortRequested || zPositionSteps != 0) {
    loadingSeekFailure("Z return fail"); return;
  }
  loadingMotionActive = false;
  loadingParked = false;
  atHomePosition = true;
  loadingReturnReady = true;
  Serial.println("<OK,LoadingReady>");
  PrintLCD("READY TO SORT", "Press START");
}

void StopMachine() {
  abortRequested = true;
  machineStarted = false;
  stopVacuumCarryPulse();
  digitalWrite(Vacuum2, LOW);
  PrintLCD("STOPPED", "Cycle aborted");
}

void ReadEndstops() {
  X_ENDSTOP_MIN = digitalRead(Xmin);
  X_ENDSTOP_MAX = digitalRead(Xmax);
  Y_ENDSTOP_MIN = digitalRead(Ymin);
  Y_ENDSTOP_MAX = digitalRead(Ymax);
  Z_ENDSTOP_MIN = digitalRead(Zmin);
  Z_ENDSTOP_MAX = digitalRead(Zmax);
}

void MotorsOnOff(boolean OnOff) {
  digitalWrite(Xenable, OnOff);
  digitalWrite(E0enable, OnOff);
  digitalWrite(Yenable, OnOff);
  digitalWrite(E1enable, OnOff);
  digitalWrite(Zenable, OnOff);
}

void DetermineAction() {
  boolean sortCommand = false;
  if (Tempval1.startsWith("Sort,")) {
    Tempval1 = Tempval1.substring(5);
    Tempval1.trim();
    sortCommand = true;

    if (Tempval1.length() == 0) {
      REPORT_ERROR(EC_UNKNOWN_CMD, "Empty sort");
      return;
    }
  }

  if (sortCommand) {
    if (!machineStarted) {
      REPORT_ERROR(EC_CMD_NOT_STARTED, "Start machine");
      PrintLCD("E507 Stopped", "Start machine!");
      return;
    }

    abortRequested = false;

    for (int i = 0; i < sizeof(MatchingValues) / sizeof(MatchingValues[0]); ++i) {
      if (Tempval1 == MatchingValues[i]) {
        atHomePosition = false;
        ForLoop(loopStart[i], loopEnd[i]);
        if (!abortRequested) atHomePosition = true;
        return;
      }
    }

    // An unrecognized Sort command must never fall back to a pickup.
    REPORT_ERROR(EC_UNKNOWN_CMD, "Unknown sort");
    return;
  }

  long manualSteps = 5;
  int commaIdx = Tempval1.indexOf(',');
  if (commaIdx > 0) {
    String commandName = Tempval1.substring(0, commaIdx);
    if (commandName == "CalibrateX1" || commandName == "CalibrateX2" ||
        commandName == "CalibrateY1" || commandName == "CalibrateY2" ||
        commandName == "CalibrateZ1" || commandName == "CalibrateZ2") {
      String stepStr = Tempval1.substring(commaIdx + 1);
      stepStr.trim();
      long parsed = stepStr.toInt();
      if (parsed > 0) manualSteps = parsed;
      Tempval1 = commandName;
    }
  }

  if (Tempval1 == "QueryFirmware") {
    Serial.println("<Firmware," + String(FIRMWARE_ID) + ">");
    return;
  }
  if (Tempval1 == "ParkForLoading") {
    ParkForLoading();
    return;
  } else if (Tempval1 == "ReturnFromLoading") {
    ReturnFromLoading();
    return;
  }

  if (Tempval1 == "StartMachine") {
    // START must NEVER reposition the head. The operator explicitly HOMEs
    // with an empty hopper or returns from park BEFORE inserting/sorting.
    if (loadingParked || loadingMotionActive || loadingRecoveryRequired) {
      REPORT_ERROR(EC_LOADING_POSITION, "Return/Home first");
      return;
    }
    if (!atHomePosition) {
      REPORT_ERROR(EC_NOT_AT_HOME, "Home first");
      PrintLCD("START BLOCKED", "HOME required");
      return;
    }
    if (zPositionSteps != 0) {
      REPORT_ERROR(EC_Z_TRAVEL_HEIGHT, "Z not at travel");
      PrintLCD("START BLOCKED", "Z not raised");
      return;
    }
    // Position already verified. Only arm sorting; do not issue motor steps.
    abortRequested = false;
    machineStarted = true;
    loadingReturnReady = false;
    Serial.println("<OK,MachineStarted>");
    PrintLCD("Machine STARTED", "Accepting cards");
    return;
  } else if (Tempval1 == "StopMachine") {
    StopMachine();
    Serial.println("<OK,MachineStopped>");
    return;
  }

  for (int i = 0; i < sizeof(MatchingValues) / sizeof(MatchingValues[0]); ++i) {
    if (Tempval1 == MatchingValues[i]) {
      if (!machineStarted) {
        REPORT_ERROR(EC_CMD_NOT_STARTED, "Start machine");
        PrintLCD("E507 Stopped", "Start machine!");
        return;
      }
      match = 1;
      atHomePosition = false;
      ForLoop(loopStart[i], loopEnd[i]);
      if (!abortRequested) atHomePosition = true;
      break;
    }
  }

  if (Tempval1 == "CalibrateX1" || Tempval1 == "CalibrateX2" ||
      Tempval1 == "CalibrateY1" || Tempval1 == "CalibrateY2" ||
      Tempval1 == "CalibrateZ1" || Tempval1 == "CalibrateZ2" ||
      Tempval1 == "HomeButton") {
    if (loadingParked || loadingMotionActive) {
      REPORT_ERROR(EC_LOADING_POSITION, "Return first");
      return;
    }
    if (machineStarted) {
      REPORT_ERROR(EC_CMD_STARTED, "Stop machine");
      PrintLCD("E508 Started", "Stop machine!");
      return;
    }
  }

  if (Tempval1.startsWith("Calibrate")) {
    atHomePosition = false;  // Jogging invalidates calibrated XY/Z pickup origin.
    loadingReturnReady = false;
  }

  if (Tempval1 == "CalibrateX1") {Move4(0,1,(short)manualSteps,0);
  } else if (Tempval1 == "CalibrateX2") {Move4(1,1,(short)manualSteps,0);
  } else if (Tempval1 == "CalibrateY1") {Move4(0,0,0,(short)manualSteps);
  } else if (Tempval1 == "CalibrateY2") {Move4(0,1,0,(short)manualSteps);
  } else if (Tempval1 == "CalibrateZ1") {Move1CalibrationDown(manualSteps,zspeed);
  } else if (Tempval1 == "CalibrateZ2") {Move1(1,manualSteps,zespeed);
  } else if (Tempval1 == "HomeButton") {Homemachine();

  } else if (Tempval1 == "QuerySensors") {
    ReadRange(1);
    ReadEndstops();
    String response = "<Sensors,range=";
    response += String((range[0] + range[1]) / 2);
    response += ",xmin=" + String(X_ENDSTOP_MIN);
    response += ",xmax=" + String(X_ENDSTOP_MAX);
    response += ",ymin=" + String(Y_ENDSTOP_MIN);
    response += ",ymax=" + String(Y_ENDSTOP_MAX);
    response += ",zmin=" + String(Z_ENDSTOP_MIN);
    response += ",zmax=" + String(Z_ENDSTOP_MAX);
    response += ",zsteps=" + String(zPositionSteps);
    response += ",home=" + String(atHomePosition ? 1 : 0);
    response += ",parked=" + String(loadingParked ? 1 : 0);
    response += ",loadready=" + String(loadingReturnReady ? 1 : 0);
    response += ",parkmode=endstops3";
    response += ",recover=" + String(loadingRecoveryRequired ? 1 : 0);
    response += ",started=" + String(machineStarted ? 1 : 0);
    response += ">";
    Serial.println(response);

  } else if (Tempval1.startsWith("SetMotor,")) {
    if (loadingParked || loadingMotionActive) {
      REPORT_ERROR(EC_LOADING_POSITION, "Return first");
      return;
    }
    if (machineStarted) {
      REPORT_ERROR(EC_CMD_STARTED, "Stop first");
      return;
    }

    int comma1 = Tempval1.indexOf(',');
    int comma2 = Tempval1.indexOf(',', comma1 + 1);
    String motor = Tempval1.substring(comma1 + 1, comma2);
    int state = Tempval1.substring(comma2 + 1).toInt();

    if (motor == "Xenable" || motor == "Yenable" || motor == "Zenable" ||
        motor == "E0enable" || motor == "E1enable") {
      atHomePosition = false;   // Motor release can lose physical position.
      loadingReturnReady = false;
    }

    if (motor == "Xenable") digitalWrite(Xenable, state);
    else if (motor == "Yenable") digitalWrite(Yenable, state);
    else if (motor == "Zenable") digitalWrite(Zenable, state);
    else if (motor == "E0enable") digitalWrite(E0enable, state);
    else if (motor == "E1enable") digitalWrite(E1enable, state);
    else if (motor == "Vacuum1") digitalWrite(Vacuum1, state);
    else if (motor == "Vacuum2") digitalWrite(Vacuum2, state);
    else if (motor == "Lights") digitalWrite(Lights, state);

    Serial.println("<OK,Motor=" + motor + ",State=" + String(state) + ">");

  } else if (Tempval1.startsWith("SetParam,")) {
    if (machineStarted) {
      REPORT_ERROR(EC_CMD_STARTED, "Stop first");
      return;
    }
    if (!atHomePosition) {
      REPORT_ERROR(EC_NOT_AT_HOME, "Home first");
      return;
    }

    int comma1 = Tempval1.indexOf(',');
    int comma2 = Tempval1.indexOf(',', comma1 + 1);
    String param = Tempval1.substring(comma1 + 1, comma2);
    int value = Tempval1.substring(comma2 + 1).toInt();

    if (param == "speed") speed = value;
    else if (param == "zspeed") zspeed = value;
    else if (param == "zespeed") zespeed = value;
    else if (param == "xcal") Xcal = value;
    else if (param == "ycal") Ycal = value;
    else if (param == "zcal") Zcal = value;
    else if (param == "pickup_thresh") pickup_threshold = value;
    else if (param == "release_thresh") release_threshold = value;
    else if (param == "hcc") HCC = value;
    else if (param == "ycc") YCourseCorrection = value;
    else if (param == "xcc") XCourseCorrection = value;

    Serial.println("<OK,Param=" + param + ",Value=" + String(value) + ">");

  } else if (Tempval1 == "QueryParams") {
    String response = "<Params";
    response += ",speed=" + String(speed);
    response += ",zspeed=" + String(zspeed);
    response += ",zespeed=" + String(zespeed);
    response += ",xcal=" + String(Xcal);
    response += ",ycal=" + String(Ycal);
    response += ",zcal=" + String(Zcal);
    response += ",pickup_thresh=" + String(pickup_threshold);
    response += ",release_thresh=" + String(release_threshold);
    response += ",hcc=" + String(HCC);
    response += ",ycc=" + String(YCourseCorrection);
    response += ",xcc=" + String(XCourseCorrection);
    response += ">";
    Serial.println(response);

  } else if (match == 1) {
    match = 0;
  } else {
    REPORT_ERROR(EC_UNKNOWN_CMD, Tempval1.substring(0,10));
  }
}

void ForLoop(byte first, byte last) {
  for (byte i = first; i <= last; i++) {
    if (abortRequested || !machineStarted) break;

    if (AssignedTrayValue[i] == "") {
      AssignedTrayValue[i] = Tempval1;
      PrintLCD("Tray assigned ", Tempval1);
      delay(10);
    }

    if (AssignedTrayValue[i] == Tempval1 && CountArray[i] <= 375) {
      Tray(i);
      if (!abortRequested && machineStarted) {
        Serial.println((String) "Went to tray" + i);
      }
      break;
    } else if (i == 34 && CountArray[34] < 375) {
      Tray(34);
      break;
    } else if (i == 34 && CountArray[34] >= 375) {
      REPORT_ERROR(EC_OVERFLOW_FULL, "Tray 34 full");
      StopMachine();
      Tempval1 = "";
      break;
    }
  }
}

void Tray(short var) {
  atHomePosition = false;
  Move1(0, initial_pickup_distance, zspeed);
  if (abortRequested) return;
  ReadRange(1);

  long pickupRangeMm = ((long)range[0] + (long)range[1]) / 2L;
  long remainingMm = pickupRangeMm - (long)PICKUP_CONTACT_MM;
  if (remainingMm < 0) remainingMm = 0;
  long pickupSteps = remainingMm * (long)Zcal;
  pickupSteps += PICKUP_COMPRESSION_STEPS;

  long remainingSafeSteps = Z_SOFT_MAX_STEPS - zPositionSteps;
  if (remainingSafeSteps < 0) remainingSafeSteps = 0;
  if (pickupSteps > remainingSafeSteps) pickupSteps = remainingSafeSteps;

  if (!pick(pickupSteps)) {
    // A normal pickup failure leaves Z at the initial 6000-step pickup height.
    // Return to home height. An emergency stop, however, stops immediately.
    if (!abortRequested) {
      Move1(1, initial_pickup_distance, zespeed);
      zPositionSteps = 0;
      atHomePosition = true;
    }
    return;
  }

  // A successful pick returns to the 6000-step sensing height. Raise the
  // remaining 6000 steps back to the pickup-cycle Z=0 travel height before
  // any X/Y movement. This keeps every carry move at the same known height.
  Move1(1, CARRY_CLEARANCE_STEPS, zespeed);
  if (abortRequested) return;
  // XY must never travel with the head below the raised Z=0 position.
  if (zPositionSteps != 0) {
    StopMachine();
    REPORT_ERROR(EC_Z_TRAVEL_HEIGHT, "Z before XY");
    return;
  }

  upcount++;
  CountArray[var]++;

  short x = 0, y = 0;
  boolean xFound = false, yFound = false;

  for (byte row = 0; row < 6 && !xFound; row++) {
    for (byte col = 0; col < 5; col++) {
      if (X[row][col] == var) {
        x = xOffsets[row];
        xFound = true;
        break;
      }
    }
  }

  for (byte row = 0; row < 4 && !yFound; row++) {
    for (byte col = 0; col < 7; col++) {
      if (Y[row][col] == var) {
        y = yOffsets[row];
        yFound = true;
        break;
      }
    }
  }

  int absX = abs(Xcal * x);
  int absY = abs(Ycal * y);
  int moveToDirectionX = (x >= 0) ? 1 : 0;
  int moveToDirectionY = (y >= 0) ? 0 : 1;
  Move4(moveToDirectionX, moveToDirectionY, absX, absY);
  if (abortRequested) return;

  // Release at the existing raised travel height; no downward drop stroke.
  if (!releaseCardAtTravelHeight()) return;
  if (abortRequested) return;

  int moveBackDirectionX = (x >= 0) ? 0 : 1;
  int moveBackDirectionY = (y >= 0) ? 1 : 0;
  // Return to pickup one axis at a time. The outward tray move remains
  // simultaneous for speed; the precision return gives X and Y their own
  // motion window so neither axis can be lost under combined load.
  if (absX > 0) {
    Move4(moveBackDirectionX, moveBackDirectionY, absX, 0);
    if (abortRequested) return;
  }
  if (absY > 0) {
    Move4(moveBackDirectionX, moveBackDirectionY, 0, absY);
    if (abortRequested) return;
  }

  // Z has remained at full travel height throughout tray release.
  // No Z correction is required before returning to pickup.
  if (y != 0) { Move4(0, 0, 0, YCourseCorrection); }
  if (x != 0) { Move4(0, 1, XCourseCorrection, 0); }
  if (abortRequested) return;

  atHomePosition = true;
}

void Move4(boolean dir1, boolean dir3, short steps1, short steps2) {
  boolean dir2;
  if (dir1 == 0) {dir2 = 1;} else {dir2 = 0;}
  digitalWrite(Xdir, dir1);
  digitalWrite(E0dir, dir2);
  digitalWrite(Ydir, dir3);
  digitalWrite(E1dir, dir3);

  for (short i = 0; (i < steps1 || i < steps2); i++) {
    if (abortRequested) break;
    if ((i % 10) == 0) updateVacuumCarryPulse();
    if ((machineStarted || loadingMotionActive) && (i % 25 == 0) && checkEmergencyStop()) break;

    if (i < steps1) {
      digitalWrite(Xstep, HIGH);
      digitalWrite(E0step, HIGH);
    }
    if (i < steps2) {
      digitalWrite(Ystep, HIGH);
      digitalWrite(E1step, HIGH);
    }
    delayMicroseconds(speed);
    digitalWrite(Xstep, LOW);
    digitalWrite(E0step, LOW);
    digitalWrite(Ystep, LOW);
    digitalWrite(E1step, LOW);
    delayMicroseconds(speed);
  }
  updateVacuumCarryPulse();
}

void Move1CalibrationDown(long steps, short speed1) {
  if (abortRequested) return;

  long availableSteps = Z_MANUAL_CAL_MAX_STEPS - zPositionSteps;
  if (availableSteps <= 0) {
    Serial.println("Z manual calibration limit reached");
    return;
  }
  if (steps > availableSteps) steps = availableSteps;

  digitalWrite(Zdir, 0);
  for (long i = 0; i < steps; i++) {
    if (abortRequested) break;

    digitalWrite(Zstep, HIGH);
    delayMicroseconds(speed1);
    digitalWrite(Zstep, LOW);
    delayMicroseconds(speed1);
    zPositionSteps++;
  }
}

void Move1(boolean dir, long steps, short speed1) {
  if (abortRequested) return;

  if (dir == 0) {
    long availableSteps = Z_SOFT_MAX_STEPS - zPositionSteps;
    if (availableSteps <= 0) {
      Serial.println("Z soft limit reached");
      return;
    }
    if (steps > availableSteps) steps = availableSteps;
  }

  digitalWrite(Zdir, dir);
  for (long i = 0; i < steps; i++) {
    if (abortRequested) break;
    if ((i % 100L) == 0) updateVacuumCarryPulse();
    if ((machineStarted || loadingMotionActive) && (i % 200L == 0) && checkEmergencyStop()) break;

    digitalWrite(Zstep, HIGH);
    delayMicroseconds(speed1);
    digitalWrite(Zstep, LOW);
    delayMicroseconds(speed1);

    if (dir == 0) {
      zPositionSteps++;
    } else {
      zPositionSteps--;
    }
  }
  updateVacuumCarryPulse();
}

void PrintLCD(String var1, String var2) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(var1);
  lcd.setCursor(0, 1);
  lcd.print(var2);
}

void ReadRange(byte var1) {
  range[var1 - 1] = vl.readRange();
  delay(10);
  range[var1] = vl.readRange();
  delay(10);
}
