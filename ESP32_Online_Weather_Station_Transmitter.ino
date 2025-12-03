#include <SPI.h>
#include <nRF24L01.h>
#include <RF24.h>
#include <Adafruit_BMP280.h>
#include <DHT.h>
#include <DHT_U.h>
#include <Wire.h>

#define uS_TO_S_FACTOR 1000000ULL  // Conversion factor for micro seconds to seconds
#define TIME_TO_SLEEP  294    
RTC_DATA_ATTR int bootCount = 0;

#define SOIL_SENSOR_PIN 36
#define RAIN_POWER_PIN 26
#define RAIN_SENSOR_PIN 39

#define DHTPIN 2
#define DHTTYPE DHT22
DHT dht(DHTPIN, DHTTYPE);

const int trigPin = 25;
const int echoPin = 33;

struct SensorPacket {
  float temperature;        // DHT22 temperature
  float humidity;           // DHT22 humidity
  int soil;                 // Soil moisture %
  float pressure;           // BMP280 pressure in kPa
  int rain;                 // Rain sensor value
  int snow;               // Snow depth
  unsigned long bootCount;  // Number of times device booted
};

RF24 radio(4, 5); // CE, CSN
Adafruit_BMP280 bmp; // I2C

const byte address[6] = "00001";

void setup() {
  Serial.begin(115200);
  dht.begin();
  bmp.begin(0x76);
  bmp.setSampling(
    Adafruit_BMP280::MODE_FORCED,
    Adafruit_BMP280::SAMPLING_X2,
    Adafruit_BMP280::SAMPLING_X4,
    Adafruit_BMP280::FILTER_X4
  );
  pinMode(RAIN_POWER_PIN, OUTPUT);
  digitalWrite(RAIN_POWER_PIN, LOW);
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);
  radio.begin();
  radio.openWritingPipe(address);
  radio.setPALevel(RF24_PA_MAX);
  radio.stopListening();
  ++bootCount;
  Serial.println("Boot number: " + String(bootCount));
  sensor ();
  esp_sleep_enable_timer_wakeup(TIME_TO_SLEEP * uS_TO_S_FACTOR);
  esp_deep_sleep_start();
}

void sensor () {
  SensorPacket data;

  for (int i = 0; i <2; i++) {
    delay (2000);

    float h = dht.readHumidity();
    float t = dht.readTemperature();
    bmp.takeForcedMeasurement();
    float hectopascal = bmp.readPressure()/100.0;

    int moist_percent = analogRead(SOIL_SENSOR_PIN);

    digitalWrite(RAIN_POWER_PIN, HIGH);
    delay(100);
    int rainValue = analogRead(RAIN_SENSOR_PIN);
    digitalWrite(RAIN_POWER_PIN, LOW);
    
    long distance = 0;
    if (t < 0 && bootCount%12 == 0) {
      digitalWrite(trigPin, LOW);
      delayMicroseconds(5);
      digitalWrite(trigPin, HIGH);
      delayMicroseconds(100);
      digitalWrite(trigPin, LOW);
      long duration = pulseIn(echoPin, HIGH);
      float speed = (331.4+0.606*t)/10000;
      distance = 101 - duration * speed / 2;
    }

    data.temperature = t;
    data.humidity = h;
    data.soil = moist_percent;
    data.pressure = hectopascal;
    data.rain = rainValue;
    data.snow = distance;
    data.bootCount = bootCount;

    Serial.print("Temp: "); Serial.print(t);
    Serial.print(", Hum: "); Serial.print(h);
    Serial.print(", Soil: "); Serial.print(moist_percent);
    Serial.print(", Pressure: "); Serial.print(hectopascal);
    Serial.print(", Rain: "); Serial.print(rainValue);
    Serial.print(", Snow: "); Serial.print(distance);
    Serial.print(", Run: "); Serial.print(i);
    Serial.print(", Boot: "); Serial.println(bootCount);
    Serial.flush();

    // Send the struct
    radio.write(&data, sizeof(data));
  }
}

void loop () {

}