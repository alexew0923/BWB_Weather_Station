# BWB_Weather_Station
This repository has information on the weather station built by Charles P. Allen High School's Robotics Club in collaboration with Better with Bees. This project is not 100% complete, so if you are referring to this project to build your own weather station, take note that it might not work the same. You can check the live data from the weather station on BWB's official website, betterwithbees.ca.

# Components Used
## Trasmitter
- XIAO ESP32C3 (<0.1mA during deep sleep, compared to ~25mA in standard ESP32)
- Perfboard
- 5V 1W Solar Panel
- 1N5819 Schottky Diode
- 2N5401 PNP Transistor
- TP4056 (battery charging module)
- 3.7V 3300mAh Lithium Ion Battery
- 2 x 10k Resistors (voltage divider to measure the battery voltage safely on ESP32)
- Battery Holder
- BMP280 (air pressure)
- Sensirion SHT40 with Weatherproof Casing (temperature & humidity)
- Capacitive Soil Moisture Sensor
- Rain Sensor (does not actually measure the rainfall amount, but functions similar to the soil moisture sensor)
- Conformal Coating
- 3D Printed Stevenson Screen
- Window Screen (extra protection against rain)
- 2" 4' PCB Pipe (to place the weather station on top of the pipe in order to prevent ground heat)

## Receiver
- XIAO ESP32C3 (main receiver via ESPNOW with the XIAO ESP32C3 in the transmitter)
- ESP32 (uploading data to Google Sheet)
- Carboard Case
- USB-C Cable

# Circuit Diagram
## Transmitter
<img src="https://github.com/alexew0923/BWB_Weather_Station/blob/main/circuit_diagrams/weather_station_v2.png" width=50% height=50%>

## Receiver
<img src="https://github.com/alexew0923/BWB_Weather_Station/blob/main/circuit_diagrams/weather_station_receiver_v2.png" width=30% height=30%>

# How It Works
1. The weather station located outside will collect meteorological data every 5 minutes such as temperature, humidity, air pressure, soil moisture and rain value.
2. The data is transmitted to the receiver at school through ESPNOW. *While any ESP32 works fine, XIAO ESP32 series has a good ESPNOW range performance.
3. The receiver module receives the data and using I2C communication, transmits the data to another ESP32 that is wired together.
4. The other ESP32 uploads it on Google Sheets through Apps Script web app.
5. Apps Script process data each night to convert them into daily averages and archives the old data.
6. Google Sheets creates different graphs and publish it on betterwithbees.ca.

# Maintenance
1. Visit the weather station regularly to see if there is any damage and clear any snow or debris left on the solar panel and rain sensor.

# Troubleshooting
Go through the list in order to troubleshoot the problem.
## No Data Is Being Received
1. Between 11pm - 6am, the receiver is turned off because the entire power goes down in the school.
2. Check the battery voltage on the website.
   - If the battery voltage is around 3.0V, the battery is dead. It can be recharged through the solar panel or it can be replaced with a new one.
4. Go to the modular and check if the receiver is unplugged.
   - If it is unplugged, plug the Chromebook charger back into the outlet.
   - Even if it is plugged in, just unplug the charger and plug it back in order to restart the receiver.

# Battery Consumption
<img src="https://github.com/alexew0923/BWB_Weather_Station/blob/main/battery_consumption.png">
*Note* The raw data is drawn by myself, but the current consumption calculation is done with Gemini using the formula: Current(mA) = Change in Battery State of Charge X Battery Capacity(3300mAh) / # of Hours
The value of state of charge of battery differs for each type of battery and the value Gemini used might not be correct.

## Change from Version 1 to Version 2
Originally, the weather station used a generic ESP32 and used NRF24L01+PA+LNA transceiver modules to send the data. However, the transmission was limited to short range and the ESP32 drew ~25mA even in deep sleep mode. I am assuming it drew that much current because the 3.7V from the battery passes through the linear voltage regulator on ESP32, which consumes quite amount of power. To address these issues, I changed the ESP32 to a specific ESP32 called XIAO ESP32 C3 manufactured by the Seeed Studio. I switched the communication between the transmitter and receiver from NRF24L01 to ESPNOW, which improves the range significantly and I find it more reliable as well. Additionally, XIAO ESP32 C3 draws less than 0.1mA of power during deep sleep, which is impressive compared to the previous result. The reason why I did not use ESPNOW with the regular ESP32 is because it does not come with an external antenna, so NRF24L01 had a longer range.
