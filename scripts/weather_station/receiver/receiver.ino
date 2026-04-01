#include <SPI.h>
#include <nRF24L01.h>
#include <RF24.h>
#include "WiFi.h"
#include <HTTPClient.h>

RF24 radio(4, 5); // CE, CSN
const byte address[6] = "80923";

struct SensorPacket { //new structure to store data
    float temperature;
    float humidity;
    int soil;
    float pressure;
    int rain;
    float battery;
    unsigned long bootCount;
};
SensorPacket receivedData;

// WiFi credentials
const char* ssid = "changeSSID"; //change SSID(Wi-Fi name)
const char* password = "changedPassword"; //change password

// Google script ID and required credentials
String GOOGLE_SCRIPT_ID = "changedGscript ID"; //change Gscript ID

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.print("Connecting to wifi: ");
  Serial.println(ssid);
  Serial.flush();
  WiFi.begin(ssid, password); //connect to WiFi
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    Serial.print(WiFi.status());
    delay(1000);
  }
  Serial.println("");

  radio.begin(); //Start NRF24L01
  radio.setChannel(76);
  radio.setDataRate(RF24_250KBPS);
  radio.setPALevel(RF24_PA_MAX);
  radio.openReadingPipe(1, address);
  radio.startListening();
}

void loop() {
  if (radio.available()) {
    Serial.println("success!");
    radio.read(&receivedData, sizeof(receivedData)); //listen to incoming transmission
    Serial.print("Temp: "); Serial.print(receivedData.temperature); //serial print for debugging purpose
    Serial.print(", Hum: "); Serial.print(receivedData.humidity);
    Serial.print(", Soil: "); Serial.print(receivedData.soil);
    Serial.print(", Pressure: "); Serial.print(receivedData.pressure);
    Serial.print(", Rain: "); Serial.print(receivedData.rain);
    Serial.print(", Battery Voltage: "); Serial.print(receivedData.battery);
    Serial.print(", Boot: "); Serial.println(receivedData.bootCount);

    if (WiFi.status() == WL_CONNECTED) { //send the data to Apps Script via web app
      static bool flag = false;
      String urlFinal = "https://script.google.com/macros/s/"+GOOGLE_SCRIPT_ID+"/exec?"+"temp=" + receivedData.temperature + "&hum=" + receivedData.humidity + "&soil=" + receivedData.soil + "&air=" + receivedData.pressure + "&rain=" + receivedData.rain + "&bat=" + receivedData.battery + "&count=" + receivedData.bootCount; //URL for web app
      Serial.print("POST data to spreadsheet:");
      Serial.println(urlFinal);
      HTTPClient http;
      http.begin(urlFinal.c_str());
      http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
      int httpCode = http.GET(); 
      Serial.print("HTTP Status Code: ");
      Serial.println(httpCode);
      //---------------------------------------------------------------------
      //getting response from google sheet
      String payload;
      if (httpCode > 0) {
        payload = http.getString();
        Serial.println("Payload: "+payload);    
      }
      //---------------------------------------------------------------------
      http.end();
    }
  }
}
