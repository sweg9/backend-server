// The hand's firmware: reads the serial commands of the RAL (src/backend/hub/ral/linear_actuators.py).
// Format and wiring: firmware/README.md. The motors are timed, not measured (no position sensor).
//
// Safety in the board itself, so the hand stops even if the hub crashes (FR-10, C-04):
// - a push is capped at MAX_PUSH_MS;
// - an actuator held out for HOLD_MAX_MS without a RELEASE goes back by itself;
// - STOP turns every motor off at once, also in the middle of a move, and PRESS is refused until RESET.
// The loop never blocks (no delay()), so STOP is read while a motor runs.

const unsigned long MAX_PUSH_MS = 2000;
const unsigned long HOLD_MAX_MS = 5000;
const unsigned long BACK_EXTRA_MS = 100;  // back runs a bit longer than out; the actuator's end switch stops it
const unsigned long HOME_MS = MAX_PUSH_MS * 6 / 5 + BACK_EXTRA_MS;  // all the way back, after RESET

struct Act {
  const char *id;
  int rpwm, lpwm, ren, len;  // BTS7960: RPWM out, LPWM back, R_EN and L_EN held high
  byte state;
  unsigned long until;   // when the current move ends (millis)
  unsigned long pushed;  // how long it went out: back takes a little longer
  unsigned long heldAt;
};

enum { IN, PUSHING, HELD, BACK };

Act acts[] = {
  {"A1", 5, 6, 7, 8, IN, 0, 0, 0},
  {"A2", 9, 10, 11, 12, IN, 0, 0, 0},  // second driver: not wired on the bench yet
};
const int N = sizeof(acts) / sizeof(acts[0]);

bool stopped = false;
bool homing = false;  // RESET: every actuator goes back, then "OK RESET"
char line[48];
byte len = 0;

void motor(Act &a, int dir) {  // 1 out, -1 back, 0 off
  analogWrite(a.rpwm, 0);
  analogWrite(a.lpwm, 0);
  if (dir > 0) analogWrite(a.rpwm, 255);
  if (dir < 0) analogWrite(a.lpwm, 255);
}

bool moving() {
  for (int i = 0; i < N; i++)
    if (acts[i].state == PUSHING || acts[i].state == BACK) return true;
  return false;
}

void led() { digitalWrite(LED_BUILTIN, moving() ? HIGH : LOW); }

Act *find(const char *id) {
  for (int i = 0; i < N; i++)
    if (strcmp(acts[i].id, id) == 0) return &acts[i];
  return NULL;
}

void back(Act &a, unsigned long ms) {
  motor(a, -1);
  a.state = BACK;
  a.until = millis() + ms;
}

void stopAll() {
  for (int i = 0; i < N; i++) {
    Act &a = acts[i];
    motor(a, 0);
    if (a.state == PUSHING) { Serial.print("ERR PRESS "); Serial.print(a.id); Serial.println(" stopped"); }
    if (a.state == BACK && !homing) { Serial.print("ERR RELEASE "); Serial.print(a.id); Serial.println(" stopped"); }
    if (a.state != IN) { a.state = HELD; a.pushed = MAX_PUSH_MS; }  // where it stands is unknown: RESET pulls it back
  }
  if (homing) Serial.println("ERR RESET stopped");
  homing = false;
  stopped = true;
  Serial.println("OK STOP");
}

void command(char *s) {
  char *cmd = strtok(s, " ");
  char *id = strtok(NULL, " ");
  char *arg = strtok(NULL, " ");
  if (cmd == NULL) return;
  if (strcmp(cmd, "STOP") == 0) { stopAll(); return; }
  if (strcmp(cmd, "PING") == 0) { Serial.println(stopped ? "OK PING stopped" : "OK PING"); return; }
  if (strcmp(cmd, "RESET") == 0) {
    if (moving()) { Serial.println("ERR RESET busy"); return; }
    stopped = false;
    homing = true;
    for (int i = 0; i < N; i++) back(acts[i], HOME_MS);
    return;
  }
  bool press = strcmp(cmd, "PRESS") == 0, release = strcmp(cmd, "RELEASE") == 0;
  if (!press && !release) { Serial.println("ERR ? unknown_command"); return; }
  Act *a = id ? find(id) : NULL;
  if (a == NULL) { Serial.print("ERR "); Serial.print(cmd); Serial.println(" ? unknown_actuator"); return; }
  #define ERR(why) { Serial.print("ERR "); Serial.print(cmd); Serial.print(" "); Serial.print(a->id); Serial.println(" " why); return; }
  if (stopped) ERR("stopped");
  if (homing || a->state == PUSHING || a->state == BACK) ERR("busy");
  if (press) {
    long ms = arg ? atol(arg) : 0;
    if (ms < 1 || (unsigned long)ms > MAX_PUSH_MS) ERR("bad_ms");
    if (a->state != IN) ERR("not_back");
    motor(*a, 1);
    a->state = PUSHING;
    a->pushed = ms;
    a->until = millis() + ms;
  } else if (a->state == IN) {
    Serial.print("OK RELEASE "); Serial.println(a->id);
  } else {
    back(*a, a->pushed * 6 / 5 + BACK_EXTRA_MS);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
  for (int i = 0; i < N; i++) {
    Act &a = acts[i];
    pinMode(a.rpwm, OUTPUT); pinMode(a.lpwm, OUTPUT);
    pinMode(a.ren, OUTPUT); pinMode(a.len, OUTPUT);
    motor(a, 0);
    digitalWrite(a.ren, HIGH);
    digitalWrite(a.len, HIGH);
  }
  Serial.println("READY");  // the board restarts when the hub opens the port
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') { line[len] = 0; command(line); len = 0; }
    else if (len < sizeof(line) - 1) line[len++] = c;
  }
  unsigned long now = millis();
  for (int i = 0; i < N; i++) {
    Act &a = acts[i];
    if (a.state == PUSHING && (long)(now - a.until) >= 0) {
      motor(a, 0);
      a.state = HELD;
      a.heldAt = now;
      Serial.print("OK PRESS "); Serial.println(a.id);
    } else if (a.state == BACK && (long)(now - a.until) >= 0) {
      motor(a, 0);
      a.state = IN;
      if (!homing) { Serial.print("OK RELEASE "); Serial.println(a.id); }
    } else if (a.state == HELD && !stopped && (long)(now - a.heldAt) >= (long)HOLD_MAX_MS) {
      Serial.print("EVT RELEASE "); Serial.print(a.id); Serial.println(" hold_timeout");
      back(a, a.pushed * 6 / 5 + BACK_EXTRA_MS);
    }
  }
  if (homing && !moving()) { homing = false; Serial.println("OK RESET"); }
  led();
}
