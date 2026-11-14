// Copy this file to secrets.h (which git ignores) and fill it in. With no secrets.h the ESP32 uses no WiFi
// and no thermal camera: the microphone works on its own.
#define WIFI_SSID "the network's name"
#define WIFI_PASS "the password"

// For a network where each person has a user name and a password (WPA2-Enterprise with PEAP, such as a
// campus network), also give the user name; WIFI_PASS is then that person's password.
// #define WIFI_EAP_USERNAME "user name"
