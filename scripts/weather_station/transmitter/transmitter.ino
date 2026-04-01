//Set CPU frequency to 80MHz to save power 
#include <SPI.h>
#include <nRF24L01.h>
#include <RF24.h>
#include <Adafruit_BMP280.h>
#include "Adafruit_SHT4x.h"
#include <Wire.h>

#define uS_TO_S_FACTOR 1000000ULL  // Conversion factor for micro seconds to seconds
#define TIME_TO_SLEEP  299 //Sleep time, currently at 5 minutes, accounting for the time it takes to run sensor()
RTC_DATA_ATTR int bootCount = 0;

#define SOIL_SENSOR_PIN 36
#define SOIL_POWER_PIN 32

#define RAIN_SENSOR_PIN 39
#define RAIN_POWER_PIN 26

Adafruit_SHT4x sht4 = Adafruit_SHT4x();

#define BATTERY_PIN 33

struct SensorPacket { //New structure to store data
  float temperature;        // DHT22 temperature
  float humidity;           // DHT22 humidity
  int soil;                 // Soil moisture %
  float pressure;           // BMP280 pressure in kPa
  int rain;                 // Rain sensor value
  float battery;              //Measures battery voltage
  unsigned long bootCount;  // Number of times device booted
};

RF24 radio(4, 5); // CE, CSN
const byte address[6] = "80923"; //Same address for the receiver

TwoWire I2COne = TwoWire(0);
TwoWire I2CTwo = TwoWire(1);

Adafruit_BMP280 bmp(&I2COne); //I2C

void setup() {
  delay(500);
  //Serial.begin(115200); //uncomment serial print lines when debugging

  I2COne.begin(21, 22);
  I2CTwo.begin(2, 16);
  sht4.begin(&I2CTwo);
  sht4.setPrecision(SHT4X_HIGH_PRECISION);
  sht4.setHeater(SHT4X_NO_HEATER);
  bmp.begin(0x76);
  bmp.setSampling(
    Adafruit_BMP280::MODE_FORCED,
    Adafruit_BMP280::SAMPLING_X2,
    Adafruit_BMP280::SAMPLING_X4,
    Adafruit_BMP280::FILTER_X4
  );
  pinMode(RAIN_POWER_PIN, OUTPUT); //Setting pins for sensors
  digitalWrite(RAIN_POWER_PIN, LOW);
  pinMode(SOIL_POWER_PIN, OUTPUT);
  digitalWrite(SOIL_POWER_PIN, LOW);

  radio.begin(); //Initializing NRF24L01
  radio.setChannel(76);
  radio.setDataRate(RF24_250KBPS);
  radio.setPALevel(RF24_PA_MAX);
  radio.setRetries(2, 3);
  radio.powerUp();
  radio.stopListening(address);
  ++bootCount;
  //Serial.println("Boot number: " + String(bootCount));
  sensor();
  radio.powerDown();
  esp_sleep_enable_timer_wakeup(TIME_TO_SLEEP * uS_TO_S_FACTOR);
  esp_deep_sleep_start();
}

void sensor() {
  SensorPacket data;

  sensors_event_t humidity, temp;
  sht4.getEvent(&humidity, &temp); //populate temp and humidity objects with fresh data
  data.humidity = humidity.relative_humidity;
  data.temperature = temp.temperature;

  bmp.takeForcedMeasurement();
  data.pressure = bmp.readPressure()/100.0;

  digitalWrite(RAIN_POWER_PIN, HIGH);
  delay(100);
  data.rain = analogRead(RAIN_SENSOR_PIN);
  digitalWrite(RAIN_POWER_PIN, LOW);
  
  digitalWrite(SOIL_POWER_PIN, HIGH);
  delay(200);
  if (bootCount%6 == 0 && data.temperature > 0) { //Measure every 30 minutes
    data.soil = analogRead(SOIL_SENSOR_PIN);
  } else {
    data.soil = 0;
  }
  digitalWrite(SOIL_POWER_PIN, LOW);

  for (int i = 0; i < 100; i++) {
    data.battery += analogReadMilliVolts(BATTERY_PIN);
  }
  data.battery = data.battery/100.0;
  data.battery = 98.6/98.4 * data.battery + data.battery - 30; //In millivolts

  data.bootCount = bootCount;

  /*Serial.print("Temp: "); Serial.print(data.temperature);
  Serial.print(", Hum: "); Serial.print(data.humidity);
  Serial.print(", Soil: "); Serial.print(data.soil);
  Serial.print(", Pressure: "); Serial.print(data.pressure);
  Serial.print(", Rain: "); Serial.print(data.rain);
  Serial.print(", Battery Voltage: "); Serial.print(data.battery);
  Serial.print(", Boot: "); Serial.println(data.bootCount);
  Serial.flush();*/

  // Send the struct
  radio.write(&data, sizeof(data));
}

void loop() {

}
