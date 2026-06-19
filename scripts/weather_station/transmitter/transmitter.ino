#include <Adafruit_BMP280.h> //library for BMP280 air pressure sensor
#include "Adafruit_SHT4x.h" //library for SHT40 temperature & humidity sensor
#include <Wire.h> //library for I2C communication
#include <esp_now.h> //library for espnow
#include <WiFi.h> //library for WiFi which is needed to run espnow

#define uS_TO_S_FACTOR 1000000ULL  //conversion factor for micro seconds to seconds
#define TIME_TO_SLEEP 300          //sleep time in seconds, currently set at 5 minutes
RTC_DATA_ATTR int bootCount = 0;

#define SOIL_SENSOR_PIN 4
#define RAIN_SENSOR_PIN 3
#define TRANSISTOR_PIN 5
#define BATTERY_PIN 2

Adafruit_SHT4x sht4 = Adafruit_SHT4x();
Adafruit_BMP280 bmp;

uint8_t broadcastAddress[] = {0xe8, 0xf6, 0x0a, 0x15, 0x19, 0xfc};
esp_now_peer_info_t peerInfo;

struct SensorPacket {       //New structure to store data
  float temperature;        // DHT22 temperature
  float humidity;           // DHT22 humidity
  int soil;                 // Soil moisture %
  float pressure;           // BMP280 pressure in kPa
  int rain;                 // Rain sensor value
  float battery;            //Measures battery voltage
  unsigned long bootCount;  // Number of times device booted
};
SensorPacket data;

void OnDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  //Serial.print("\r\nLast Packet Send Status:\t");
  //Serial.println(status == ESP_NOW_SEND_SUCCESS ? "Delivery Success" : "Delivery Fail");
}

void setup() {
  delay(500) //prevent infinte deep sleep bug
  //Serial.begin(115200);  //uncomment serial print lines when debugging
  Wire.begin(6, 7);
  sht4.begin(&Wire);
  sht4.setPrecision(SHT4X_HIGH_PRECISION);
  sht4.setHeater(SHT4X_NO_HEATER);
  bmp.begin(0x76);
  bmp.setSampling(
    Adafruit_BMP280::MODE_FORCED,
    Adafruit_BMP280::SAMPLING_X2,
    Adafruit_BMP280::SAMPLING_X4,
    Adafruit_BMP280::FILTER_X4);
  pinMode(TRANSISTOR_PIN, OUTPUT);
  digitalWrite(TRANSISTOR_PIN, HIGH);
  
  WiFi.mode(WIFI_STA);

  // Init ESP-NOW
  if (esp_now_init() != ESP_OK) {
    //Serial.println("Error initializing ESP-NOW");
    return;
  }

  // Once ESPNow is successfully Init, we will register for Send CB to
  // get the status of Trasnmitted packet
  esp_now_register_send_cb(esp_now_send_cb_t(OnDataSent));
  
  // Register peer
  memcpy(peerInfo.peer_addr, broadcastAddress, 6);
  peerInfo.channel = 0;  
  peerInfo.encrypt = false;
  
  // Add peer        
  if (esp_now_add_peer(&peerInfo) != ESP_OK){
    //Serial.println("Failed to add peer");
    return;
  }

  ++bootCount;
  //Serial.println("Boot number: " + String(bootCount));
  sensor();
  esp_sleep_enable_timer_wakeup(TIME_TO_SLEEP * uS_TO_S_FACTOR);
  delay(10);
  esp_deep_sleep_start();
}

void sensor() {
  sensors_event_t humidity, temp;
  sht4.getEvent(&humidity, &temp);  //populate temp and humidity objects with fresh data
  data.humidity = humidity.relative_humidity;
  data.temperature = temp.temperature;

  bmp.takeForcedMeasurement();
  data.pressure = bmp.readPressure() / 100.0;
  
  digitalWrite(TRANSISTOR_PIN, LOW);
  delay(10);
  data.rain = analogRead(RAIN_SENSOR_PIN);
  data.soil = analogRead(SOIL_SENSOR_PIN);
  digitalWrite(TRANSISTOR_PIN, HIGH);

  for (int i = 0; i < 100; i++) { //read battery voltage 100 times to average it out
    data.battery += analogReadMilliVolts(BATTERY_PIN);
  }
  data.battery = data.battery / 100.0;
  data.battery = 98.6 / 98.4 * data.battery + data.battery + 35;  //battery voltage in millivolts with 35mV offset

  data.bootCount = bootCount;

  esp_err_t result = esp_now_send(broadcastAddress, (uint8_t *) &data, sizeof(data));
   
  if (result == ESP_OK) {
    //Serial.println("Sent with success");
  }
  else {
    //Serial.println("Error sending the data");
  }

  /*Serial.print("Temp: ");
  Serial.print(data.temperature);
  Serial.print(", Hum: ");
  Serial.print(data.humidity);
  Serial.print(", Soil: ");
  Serial.print(data.soil);
  Serial.print(", Pressure: ");
  Serial.print(data.pressure);
  Serial.print(", Rain: ");
  Serial.print(data.rain);
  Serial.print(", Battery Voltage: ");
  Serial.print(data.battery);
  Serial.print(", Boot: ");
  Serial.println(data.bootCount);
  Serial.flush();*/
}

void loop() {
}
