const sheet_id = "1iJzvixnEx5QH2lkQNkN8xKZqpIyGO7FmEsa_qsyHCOI"; //get the Google Sheets and individual sheets
const ss = SpreadsheetApp.openById(sheet_id);
const sheet = ss.getSheetByName("weather_station");
const archiveSheet = ss.getSheetByName("ArchivedAverages");
const numberSheet = ss.getSheetByName("Number_of_Data_Received");
const historicalSheet = ss.getSheetByName("HistoricalData");
const graphSheet = ss.getSheetByName("Graphs");

function doGet(e){ //executed when the receiver sends the URL
  const temp = String(e.parameter.temp); //get individual data from the URL
  const hum = String(e.parameter.hum);
  const soil = String(e.parameter.soil);
  const air = String(e.parameter.air);
  const rain = String(e.parameter.rain);
  const battery = String(e.parameter.bat);
  const count = String(e.parameter.count);
  const time = new Date();

  const values = [[time, temp, hum, soil, air, rain, battery, count]];
  for (let i = 1; i < 7; i++) {
    if (isNaN(values[0][i]) || values[0][i] == 0) { //check for NaN values and 0 for every other data except the temperature
      values[0][i] = ""; //if that is the case, set the value to empty
    }
  }

  Logger.log(values);

  if (count != 0) {
    sheet.getRange(sheet.getLastRow() + 1, 1, 1, 8).setValues(values);
    const graphTitle = ["Temperature (°C)", "Air Pressure (hPa)", "Battery Voltage (mV)"]
    const graphRange = ["B2:B", "E2:E", "G2:G"]
    changeYAxis(graphTitle, graphRange, "weather_station");
  }
}
