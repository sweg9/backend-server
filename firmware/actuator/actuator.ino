const int RPWM = 5, LPWM = 6, R_EN = 7, L_EN = 8;
const float SPEED_MM_S = 8.0;

void setup() {
  pinMode(RPWM, OUTPUT); pinMode(LPWM, OUTPUT);
  pinMode(R_EN, OUTPUT); pinMode(L_EN, OUTPUT);
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(R_EN, HIGH);
  digitalWrite(L_EN, HIGH);
}

void stopAct() { analogWrite(RPWM, 0); analogWrite(LPWM, 0); }

void moveMm(bool forward, float mm) {
  digitalWrite(LED_BUILTIN, HIGH);
  analogWrite(forward ? LPWM : RPWM, 0);
  analogWrite(forward ? RPWM : LPWM, 255);
  delay((unsigned long)(mm / SPEED_MM_S * 1000));
  stopAct();
  digitalWrite(LED_BUILTIN, LOW);
}

void loop() {
  moveMm(true, 20);
  delay(1000);
  moveMm(false, 20);
  delay(1000);
}