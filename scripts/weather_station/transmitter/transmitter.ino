//Set CPU frequency to 80MHz to save power 
#include <SPI.h>
#include <nRF24L01.h>
#include <RF24.h>
#include <Adafruit_BMP280.h>
#include <DHT.h>
#include <DHT_U.h>
#include <Wire.h>

#define uS_TO_S_FACTOR 1000000ULL  // Conversion factor for micro seconds to seconds
#define TIME_TO_SLEEP  299 //Sleep time, currently at 5 minutes, accounting for the time it takes to run sensor()
RTC_DATA_ATTR int bootCount = 0;

#define SOIL_SENSOR_PIN 36
#define trigPin 25 //Ultrasonic sensor to measure the snow depth
#define echoPin 33
#define SOIL_SNOW_POWER_PIN 32

#define RAIN_SENSOR_PIN 39
#define RAIN_POWER_PIN 26

#define DHTPIN 16
#define DHTTYPE DHT22
DHT dht(DHTPIN, DHTTYPE);

struct SensorPacket { //New structure to store data
  float temperature;        // DHT22 temperature
  float humidity;           // DHT22 humidity
  int soil;                 // Soil moisture %
  float pressure;           // BMP280 pressure in kPa
  int rain;                 // Rain sensor value
  int snow;               // Snow depth
  unsigned long bootCount;  // Number of times device booted
};

RF24 radio(4, 5); // CE, CSN
const byte address[6] = "80923"; //Same address for the receiver

Adafruit_BMP280 bmp; // I2C

void setup() {
  delay(500);
  //Serial.begin(115200); //uncomment serial print lines when debugging
  dht.begin();
  bmp.begin(0x76);
  bmp.setSampling(
    Adafruit_BMP280::MODE_FORCED,
    Adafruit_BMP280::SAMPLING_X2,
    Adafruit_BMP280::SAMPLING_X4,
    Adafruit_BMP280::FILTER_X4
  );
  pinMode(RAIN_POWER_PIN, OUTPUT); //Setting pins for sensors
  digitalWrite(RAIN_POWER_PIN, LOW);
  pinMode(SOIL_SNOW_POWER_PIN, OUTPUT);
  digitalWrite(SOIL_SNOW_POWER_PIN, LOW);
  pinMode(trigPin, OUTPUT);
  digitalWrite(trigPin, LOW);
  pinMode(echoPin, INPUT);
  radio.begin(); //Initializing NRF24L01
  radio.openWritingPipe(address);
  radio.setPALevel(RF24_PA_MAX);
  radio.stopListening();
  ++bootCount;
  //Serial.println("Boot number: " + String(bootCount));
  sensor();
  esp_sleep_enable_timer_wakeup(TIME_TO_SLEEP * uS_TO_S_FACTOR);
  esp_deep_sleep_start();
}

void sensor() {
  SensorPacket data;

  float h = dht.readHumidity();
  float t = dht.readTemperature();

  bmp.takeForcedMeasurement();
  float hectopascal = bmp.readPressure()/100.0;

  digitalWrite(RAIN_POWER_PIN, HIGH);
  delay(100);
  int rainValue = analogRead(RAIN_SENSOR_PIN);
  digitalWrite(RAIN_POWER_PIN, LOW);
  
  long distance = 0;
  int moist_percent = 0;
  digitalWrite(SOIL_SNOW_POWER_PIN, HIGH);
  delay(200);
  if (t < 5) { //Measure snow depth when temperature is below five degrees zero and soil moisture is it is above zero
    if (bootCount%6 == 0) { //Measure every 30 minutes
      digitalWrite(trigPin, LOW);
      delayMicroseconds(5);
      digitalWrite(trigPin, HIGH);
      delayMicroseconds(100);
      digitalWrite(trigPin, LOW);
      long duration = pulseIn(echoPin, HIGH);
      float speed = (331.4+0.606*t)/10000;
      distance = duration * speed / 2;
    }
  }

  if (t > 0) {
    moist_percent = analogRead(SOIL_SENSOR_PIN);
  }
  digitalWrite(SOIL_SNOW_POWER_PIN, LOW);

  data.temperature = t;
  data.humidity = h;
  data.soil = moist_percent;
  data.pressure = hectopascal;
  data.rain = rainValue;
  data.snow = distance;
  data.bootCount = bootCount;

  /*Serial.print("Temp: "); Serial.print(t);
  Serial.print(", Hum: "); Serial.print(h);
  Serial.print(", Soil: "); Serial.print(moist_percent);
  Serial.print(", Pressure: "); Serial.print(hectopascal);
  Serial.print(", Rain: "); Serial.print(rainValue);
  Serial.print(", Snow: "); Serial.print(distance);
  Serial.print(", Boot: "); Serial.println(bootCount);
  Serial.flush();*/

  // Send the struct
  radio.write(&data, sizeof(data));
}

void loop() {

}
