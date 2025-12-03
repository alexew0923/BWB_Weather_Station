Weather Station and Receiver Codes/

#include <SPI.h>
#include <nRF24L01.h>
#include <RF24.h>
#include "WiFi.h"
#include <HTTPClient.h>

RF24 radio(4, 5); // CE, CSN
const byte address[6] = "00001";

struct SensorPacket {
    float temperature;
    float humidity;
    int soil;
    float pressure;
    int rain;
    int snow;
    unsigned long bootCount;
};
SensorPacket receivedData;

// WiFi credentials
const char* ssid = "HighSchool_Public";         // change SSID
const char* password = "love2learn";    // change password
// Google script ID and required credentials
String GOOGLE_SCRIPT_ID = "AKfycbw-Imw_tREJt55vgcxns3K6qF6R21QOZW4tNe3MwQtw2Ql6WUo5ioBKTGESXjLFZ6hk";    // change Gscript ID

void setup() {
  Serial.begin(9600);
  Serial.println();
  Serial.print("Connecting to wifi: ");
  Serial.println(ssid);
  Serial.flush();
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    Serial.print(WiFi.status());
    delay(1000);
  }
  Serial.println("");

  radio.begin();
  radio.openReadingPipe(0, address);
  radio.setPALevel(RF24_PA_MAX);
  radio.startListening();
}

void loop() {
  if (radio.available()) {
    Serial.println("success!");
    radio.read(&receivedData, sizeof(receivedData));
    Serial.print("Temp: "); Serial.print(receivedData.temperature);
    Serial.print(", Hum: "); Serial.print(receivedData.humidity);
    Serial.print(", Soil: "); Serial.print(receivedData.soil);
    Serial.print(", Pressure: "); Serial.print(receivedData.pressure);
    Serial.print(", Rain: "); Serial.print(receivedData.rain);
    Serial.print(", Snow: "); Serial.print(receivedData.snow);
    Serial.print(", Boot: "); Serial.println(receivedData.bootCount);

    if (WiFi.status() == WL_CONNECTED) {
      static bool flag = false;
      String urlFinal = "https://script.google.com/macros/s/"+GOOGLE_SCRIPT_ID+"/exec?"+"temp=" + receivedData.temperature + "&hum=" + receivedData.humidity + "&soil=" + receivedData.soil + "&air=" + receivedData.pressure + "&rain=" + receivedData.rain + "&snow=" + receivedData.snow + "&count=" + receivedData.bootCount;
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
