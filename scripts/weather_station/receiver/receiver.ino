#include <esp_now.h>
#include <WiFi.h>
#include "Wire.h"

struct SensorPacket {       //New structure to store data
  float temperature;        // SHT40 temperature
  float humidity;           // SHT40 humidity
  int soil;                 // Soil moisture %
  float pressure;           // BMP280 pressure in hPa
  int rain;                 // Rain sensor value
  float battery;            // Measures battery voltage
  unsigned long bootCount;  // Number of times device booted
};
SensorPacket receivedData;

// callback function that will be executed when data is received
void OnDataRecv(const uint8_t * mac, const uint8_t *incomingData, int len) {
  memcpy(&receivedData, incomingData, sizeof(receivedData));
  Serial.print("Bytes received: ");
  Serial.println(len);
  Serial.print("Temp: ");
  Serial.print(receivedData.temperature);
  Serial.print(", Hum: ");
  Serial.print(receivedData.humidity);
  Serial.print(", Soil: ");
  Serial.print(receivedData.soil);
  Serial.print(", Pressure: ");
  Serial.print(receivedData.pressure);
  Serial.print(", Rain: ");
  Serial.print(receivedData.rain);
  Serial.print(", Battery Voltage: ");
  Serial.print(receivedData.battery);
  Serial.print(", Boot: ");
  Serial.println(receivedData.bootCount);
  Serial.flush();

  // Write message to the slave
  Wire.beginTransmission(0x55); //transmit data with tag alphabets
  Wire.print("t"); Wire.print(receivedData.temperature);
  Wire.print(",h"); Wire.print(receivedData.humidity);
  Wire.print(",s"); Wire.print(receivedData.soil);
  Wire.print(",p"); Wire.print(receivedData.pressure);
  Wire.print(",r"); Wire.print(receivedData.rain);
  Wire.print(",b"); Wire.print(receivedData.battery);
  Wire.print(",c"); Wire.print(receivedData.bootCount);
  Wire.print(",");
  
  uint8_t error = Wire.endTransmission(true);
  Serial.printf("endTransmission: %u\n", error);
}

void setup() {
  // Initialize Serial Monitor
  Serial.begin(115200);
  Wire.begin(); //I2C communication to send the data to another ESP32, where it will upload it to Google Sheet

  // Set device as a Wi-Fi Station
  WiFi.mode(WIFI_STA);

  // Init ESP-NOW
  if (esp_now_init() != ESP_OK) {
    Serial.println("Error initializing ESP-NOW");
    return;
  }
  
  // Once ESPNow is successfully Init, we will register for recv CB to
  // get recv packer info
  esp_now_register_recv_cb(esp_now_recv_cb_t(OnDataRecv));
}
 
void loop() {

}
