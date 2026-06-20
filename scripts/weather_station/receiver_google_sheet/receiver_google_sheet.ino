#include "Wire.h"
#include "WiFi.h"
#include <HTTPClient.h>

char message[9];
char initial[2];

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

// WiFi credentials
const char* ssid = "HighSchool_Public"; //change SSID (WiFi name)
const char* password = "love2learn"; //change password

// Google script ID and required credentials
String GOOGLE_SCRIPT_ID = "paste your Google Script ID here"; //change Gscript ID found in Apps Script

void onReceive(int len) {
  Serial.printf("onReceive[%d]: ", len);
  while (Wire.available()) {
    for (int i = 0; i < 8; i++) { //clear message array
      message[i] = '\0';
    }
    Wire.readBytesUntil(',', message, 8); //read data and store it to message
    initial[0] = message[0]; //store the tag alphabet
    for (int i = 1; i < strlen(message); i++) { //delete the tag alphabet from the message
      message[i - 1] = message[i];
    }
    message[strlen(message) - 1] = '\0';

    if (initial[0] == 't') { //save message accordingly
      receivedData.temperature = atof(message);
    } else if (initial[0] == 'h') {
      receivedData.humidity = atof(message);
    } else if (initial[0] == 's') {
      receivedData.soil = atof(message);
    } else if (initial[0] == 'p') {
      receivedData.pressure = atof(message);
    } else if (initial[0] == 'r') {
      receivedData.rain = atof(message);
    } else if (initial[0] == 'b') {
      receivedData.battery = atof(message);
    } else if (initial[0] == 'c') {
      receivedData.bootCount = atof(message);
    }
  }
  
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

  Serial.println("success!");
  
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
  } else {
    WiFi.disconnect(); //if WiFi is not connected, disconnect then try to reconnect
    delay(1000);
    connectWiFi();
  }
}

void connectWiFi() {
  int reconnectCount;
  WiFi.begin(ssid, password); //connect to WiFi
  while (WiFi.status() != WL_CONNECTED) {
    if (reconnectCount > 300) { //if it takes more than 5 minutes to connect, restart the device
      ESP.restart();
    }
    Serial.print(".");
    Serial.print(WiFi.status());
    reconnectCount ++;
    delay(1000);
  }
}

void setup() {
  Serial.begin(115200);
  Wire.onReceive(onReceive);
  Wire.begin(0x55); //I2C communication

  Serial.print("Connecting to wifi: ");
  Serial.println(ssid);
  Serial.flush();
  connectWiFi();
  Serial.println();
}

void loop() {
  
}
