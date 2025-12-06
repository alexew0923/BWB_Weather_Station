# BWB_Weather_Station
This repository has information on the weather station built by Better with Bees and Robotics called the MONITOR.

# Components Used
## Trasmitter
- ESP32
- Perfboard
- 5V 1W Solar Panel
- 1N4007 Diode
- TP4056 (battery charging module)
- 3.7V 3300mAh Lithium Ion Battery
- Battery Holder
- Boost Converter (to convert 3.7V from the battery to 5V)
- NRF24L01 (wireless transceiver module with antenna)
- BME280 (humidity, temperature and air pressure)
- Capacitive Soil Moisture Sensor
- Rain Sensor (functions similar to the soil moisture sensor)
- Waterproof Ultrasonic Sensor (snow depth)
- Conformal Coating
- 3D printed Stevenson Screen
- 2" 4' PCB Pipe (to place the weather station on top of the pipe in order to prevent ground heat)

## Receiver
- ESP32
- Solderless Breadboard
- NRF24L01 (wireless transceiver module with antenna)
- Carboard Case

# Circuit Diagram
![Circuit Diagram of the Weather Station](/assets/images/weather_station_circuit.png)
