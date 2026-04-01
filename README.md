# BWB_Weather_Station
This repository has information on the weather station built by Charles P. Allen High School's Robotics Club in collaboration with Better with Bees. This project is not 100% complete, so if you are referring to this project to build your own weather station, take note that it might not work the same. You can check the live data from the weather station on BWB's official website, betterwithbees.ca.

# Components Used
## Trasmitter
- ESP32
- Perfboard
- 5V 1W Solar Panel
- 1N4007 Diode
- TP4056 (battery charging module)
- 3.7V 3300mAh Lithium Ion Battery
- 2 x 10k Resistors (voltage divider to measure the battery voltage safely on ESP32)
- Battery Holder
- Boost Converter (to convert 3.7V from the battery to 5V for ESP32)
- NRF24L01 (wireless transceiver module with antenna)
- BMP280 (air pressure)
- Sensirion SHT40 (temperature & humidity)
- Capacitive Soil Moisture Sensor
- Rain Sensor (does not actually measure the rainfall amount, but functions similar to the soil moisture sensor)
- Conformal Coating
- 3D Printed Stevenson Screen
- Window Screen (extra protection against rain)
- 2" 4' PCB Pipe (to place the weather station on top of the pipe in order to prevent ground heat)

## Receiver
- ESP32
- Solderless Breadboard
- NRF24L01 (wireless transceiver module with antenna)
- Carboard Case
- USB-C Cable

# Circuit Diagram
## Transmitter
<img src="https://raw.githubusercontent.com/alexew0923/BWB_Weather_Station/refs/heads/main/circuit_diagrams/weather_station_circuit_v2.png" width=50% height=50%>

## Receiver
<img src="https://github.com/alexew0923/BWB_Weather_Station/blob/main/circuit_diagrams/weather_station_receiver_circuit.png" width=30% height=30%>

# How It Works
1. The weather station located outside will collect meteorological data every 5 minutes such as temperature, humidity, air pressure, soil moisture and rain value.
2. The data is transmitted to the receiver at school via NRF24L01.
3. The receiver module receives the data and uploads it on Google Sheets through Apps Script web app.
4. Apps Script process data each night to convert them into daily averages and archives the old data.
5. Google Sheets creates different graphs and publish it on betterwithbees.ca.

# Maintenance
1. Visit the weather station regularly to see if there is any damage and clear any snow or debris left on the solar panel and rain sensor.

# Troubleshooting
Go through the list in order to troubleshoot the problem.
## No Data Is Being Received
1. Between 3pm to 4pm on weekdays, the data is not transmitted due to the noise from the school bus radios.
2. Check the battery voltage on the website.
   - If the battery voltage is around 3.0V, the battery is dead. It can be recharged through the solar panel or it can be replaced with a new one.
4. Go to the modular and check if the receiver is unplugged.
   - If it is unplugged, plug the Chromebook charger back into the outlet.
