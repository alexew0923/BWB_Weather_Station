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
  const snow = String(e.parameter.snow);
  const count = String(e.parameter.count);
  const time = new Date();

  const values = [time, temp, hum, soil, air, rain, snow, count];
  for (let i = 1; i < 7; i++) {
    if (isNaN(values[i]) || i > 1 && values[i] == 0) { //check for NaN values and 0 for every other data except the temperature
      values[i] = ""; //if that is the case, set the value to empty
    }
  }

  Logger.log(values);

  if (count != 0) {
    sheet.appendRow(values);
    const graphTitle = ["Temperature (°C)", "Air Pressure (hPa)", "Snow Depth (cm)"]
    const graphRange = ["B2:B", "E2:E", "G2:G"]
    changeYAxis(graphTitle, graphRange, "weather_station");
  }
}
