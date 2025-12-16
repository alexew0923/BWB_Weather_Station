# BWB_Weather_Station
This repository has information on the weather station built by Charles P. Allen High School's Robotics Club in collaboration with Better with Bees.

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
- BMP280 (air pressure)
- DHT22 (temperature & humidity)
- Capacitive Soil Moisture Sensor
- Rain Sensor (functions similar to the soil moisture sensor)
- JSN-SR04T Waterproof Ultrasonic Sensor (snow depth)
- Conformal Coating
- 3D printed Stevenson Screen
- Window Screen
- 2" 4' PCB Pipe (to place the weather station on top of the pipe in order to prevent ground heat)

## Receiver
- ESP32
- Solderless Breadboard
- NRF24L01 (wireless transceiver module with antenna)
- Carboard Case
- USB-C Cable

# Circuit Diagram
## Transmitter
<img src="https://github.com/alexew0923/BWB_Weather_Station/blob/main/circuit_diagrams/weather_station_circuit.png" width=50% height=50%>

## Receiver
<img src="https://github.com/alexew0923/BWB_Weather_Station/blob/main/circuit_diagrams/weather_station_receiver_circuit.png" width=50% height=50%>
