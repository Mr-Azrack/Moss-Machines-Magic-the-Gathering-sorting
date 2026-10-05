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
boolean atHomePosition = true;
boolean machineStarted = false;
boolean abortRequested = false;
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
const long Z_SOFT_MAX_STEPS = 92500L;
const short PICKUP_CONTACT_MM = 5;
const byte MAX_PICKUP_ATTEMPTS = 3;
const unsigned short PICKUP_VACUUM_BUILD_MS = 1000;
const unsigned short RELEASE_PULSE_MS = 250;

short initial_pickup_distance = 6000, initial_drop_distance = 4000;
short Xcal = 350, Ycal = 475, Zcal = 935;
short speed = 700, zspeed = 50, zespeed = 70;
short pickup_threshold = 40, release_threshold = 40;
short HCC = 10, YCourseCorrection = 1, XCourseCorrection = 0;

void setup() {
  byte pins[] = { Xstep, Ystep, Zstep, E0step, E1step, Xdir, Ydir, Zdir, E0dir, E1dir, Xenable, Yenable, Zenable, E0enable, E1enable, Vacuum1, Vacuum2, Lights};
  for (byte pin : pins) { pinMode(pin, OUTPUT); }

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
  Homemachine();
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

boolean checkEmergencyStop() {
  if (abortRequested) return true;
  if (!machineStarted || Serial.available() <= 0) return false;

  String incoming = Serial.readString();
  incoming.trim();
  if (incoming == "StopMachine") {
    abortRequested = true;
    machineStarted = false;
    digitalWrite(Vacuum1, LOW);
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
    if (abortRequested) return false;
    if (machineStarted && checkEmergencyStop()) return false;
    delay(10);
  }
  return true;
}

boolean pick(long steps, byte Release) {
  if (Release == 1) {
    Move1(0, steps, zspeed);
    if (abortRequested) return false;

    digitalWrite(Vacuum1, LOW);
    if (!delayWithStop(100)) return false;

    MotorsOnOff(1);
    if (!delayWithStop(100)) return false;

    digitalWrite(Vacuum2, HIGH);
    if (!delayWithStop(RELEASE_PULSE_MS)) {
      digitalWrite(Vacuum2, LOW);
      return false;
    }
    digitalWrite(Vacuum2, LOW);

    MotorsOnOff(0);
    if (!delayWithStop(100)) return false;
    Move1(1, steps, zespeed);
    return !abortRequested;
  }

  PickupRetry = 0;
  while (PickupRetry < MAX_PICKUP_ATTEMPTS) {
    Move1(0, steps, zspeed);
    if (abortRequested) return false;

    digitalWrite(Vacuum1, HIGH);
    if (!delayWithStop(PICKUP_VACUUM_BUILD_MS)) {
      digitalWrite(Vacuum1, LOW);
      return false;
    }

    Move1(1, steps, zespeed);
    if (abortRequested) {
      digitalWrite(Vacuum1, LOW);
      return false;
    }

    ReadRange(3);
    if (((range[2] + range[3]) / 2) <= pickup_threshold) {
      // Successful pickup: Vac1 intentionally remains ON through X/Y travel.
      return true;
    }

    digitalWrite(Vacuum1, LOW);
    PickupRetry++;
    if (PickupRetry < MAX_PICKUP_ATTEMPTS) {
      if (!delayWithStop(150)) return false;
    }
  }

  digitalWrite(Vacuum1, LOW);
  digitalWrite(Vacuum2, LOW);
  machineStarted = false;
  REPORT_ERROR(EC_PICKUP_RETRY, "3 retries failed");
  PrintLCD("E505 Pickup", "Cycle stopped");
  return false;
}

void Homemachine() {
  PrintLCD("Calibrating", " ");
  atHomePosition = false;
  ReadEndstops();

  if (Z_ENDSTOP_MIN == 1) {
    unsigned long t0 = millis();
    while (Z_ENDSTOP_MIN == 1) {
      Move1(1, 3, zespeed);
      Z_ENDSTOP_MIN = digitalRead(Zmin);
      if (millis() - t0 > 60000UL) {
        REPORT_ERROR(EC_HOME_Z_FAIL, "Z endstop");
        PrintLCD("E512 Z home", "Motion aborted");
        return;
      }
    }
    delay(200);
  }

  if (Y_ENDSTOP_MIN == 1) {
    Move4(1, 0, 0, 50);
    unsigned long t1 = millis();
    while (Y_ENDSTOP_MIN == 1) {
      Move4(0, 1, 0, 5);
      Y_ENDSTOP_MIN = digitalRead(Ymin);
      if (millis() - t1 > 10000) {
        REPORT_ERROR(EC_HOME_Y_FAIL, "Y endstop");
        break;
      }
    }
    delay(200);
  }

  if (X_ENDSTOP_MAX == 1) {
    Move4(1, 0, 50, 0);
    unsigned long t2 = millis();
    while (X_ENDSTOP_MAX == 1) {
      Move4(0, 1, 3, 0);
      X_ENDSTOP_MAX = digitalRead(Xmax);
      if (millis() - t2 > 10000) {
        REPORT_ERROR(EC_HOME_X_FAIL, "X endstop");
        break;
      }
    }
    delay(200);
  }

  Move1(0, 3000, zspeed);
  delay(200);
  Move4(1, 0, Xcal * 3 + 55, Ycal * 2 + 38);
  delay(200);
  zPositionSteps = 0;
  abortRequested = false;
  atHomePosition = true;
}

void StopMachine() {
  abortRequested = true;
  machineStarted = false;
  digitalWrite(Vacuum1, LOW);
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

    atHomePosition = false;
    ForLoop(1, 34);
    if (!abortRequested) atHomePosition = true;
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

  if (Tempval1 == "StartMachine") {
    abortRequested = false;
    machineStarted = true;
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
    if (machineStarted) {
      REPORT_ERROR(EC_CMD_STARTED, "Stop machine");
      PrintLCD("E508 Started", "Stop machine!");
      return;
    }
  }

  if (Tempval1 == "CalibrateX1") {Move4(0,1,(short)manualSteps,0);
  } else if (Tempval1 == "CalibrateX2") {Move4(1,1,(short)manualSteps,0);
  } else if (Tempval1 == "CalibrateY1") {Move4(0,0,0,(short)manualSteps);
  } else if (Tempval1 == "CalibrateY2") {Move4(0,1,0,(short)manualSteps);
  } else if (Tempval1 == "CalibrateZ1") {Move1(0,manualSteps,zspeed);
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
    response += ",started=" + String(machineStarted ? 1 : 0);
    response += ">";
    Serial.println(response);

  } else if (Tempval1.startsWith("SetMotor,")) {
    if (machineStarted) {
      REPORT_ERROR(EC_CMD_STARTED, "Stop first");
      return;
    }

    int comma1 = Tempval1.indexOf(',');
    int comma2 = Tempval1.indexOf(',', comma1 + 1);
    String motor = Tempval1.substring(comma1 + 1, comma2);
    int state = Tempval1.substring(comma2 + 1).toInt();

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

  long remainingSafeSteps = Z_SOFT_MAX_STEPS - zPositionSteps;
  if (remainingSafeSteps < 0) remainingSafeSteps = 0;
  if (pickupSteps > remainingSafeSteps) pickupSteps = remainingSafeSteps;

  if (!pick(pickupSteps, 0)) {
    // A normal pickup failure leaves Z at the initial 6000-step pickup height.
    // Return to home height.  An emergency stop, however, stops immediately.
    if (!abortRequested) {
      Move1(1, initial_pickup_distance, zespeed);
      zPositionSteps = 0;
      atHomePosition = true;
    }
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

  if (!pick(initial_drop_distance, 1)) return;
  if (abortRequested) return;

  int moveBackDirectionX = (x >= 0) ? 0 : 1;
  int moveBackDirectionY = (y >= 0) ? 1 : 0;
  Move4(moveBackDirectionX, moveBackDirectionY, absX, absY);
  if (abortRequested) return;

  Move1(1, initial_pickup_distance, zespeed);
  if (abortRequested) return;

  if (y != 0) { Move4(0, 0, 0, YCourseCorrection); }
  if (x != 0) { Move4(0, 1, XCourseCorrection, 0); }
  if (abortRequested) return;

  if (upcount % HCC == 0 && upcount >= HCC / 2) {
    Homemachine();
  }
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
    if (machineStarted && (i % 25 == 0) && checkEmergencyStop()) break;

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
    if (machineStarted && (i % 200L == 0) && checkEmergencyStop()) break;

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
