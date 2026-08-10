function archiveOldSensorData() { //executed every night between 1am to 2am to calculate the average of the data received in the previous day
  const data = sheet.getDataRange().getValues();
  if (data.length < 2) return; //return if there is no data to process

  const date = new Date();
  // Create a new Date object with only year, month, day
  const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  
  const newData = [];
  const oldData = [];
  const values = new Array(6);
  const successRate = new Array(7);
  let numRows = [0, 0, 0, 0, 0, 0];
  let transmissions = 0;

  for (let i = 1; i < data.length; i++) {
    const row = data[i];
    Logger.log(row);
    const timestamp = new Date(row[0]);
    
    if (timestamp < dateOnly) { //check if the data received is today (false) or yesterday (true)
      oldData.push(row);
      for (let j = 0; j < 6; j++) {
        if (row[j + 1] !== "") { //if the cell value is not empty, add them up
          if (numRows[j] == 0) {
            values[j] = row[j+1];
          } else {
            values[j] += row[j+1];
          }
          numRows[j] ++; //count how many times the data is received
        }
      }
      transmissions ++;
    } else {
      newData.push(row); // Keep recent row
    }
  }

  // Compute and store averages
  for (let i = 0; i < 6; i++) {
    if (numRows[i] > 0) {
      values[i] = values[i]/numRows[i];
    }
  }
  
  // Calculate success rate for each sensor
  successRate[0] = transmissions / 204;
  for (let i = 1; i < 7; i++) {
    successRate[i] = numRows[i - 1] / transmissions;
  }

  dateOnly.setDate(dateOnly.getDate() - 1); 
  archiveSheet.appendRow([dateOnly, ...values]);
  numberSheet.appendRow([dateOnly, ...numRows, transmissions, ...successRate]);

  Logger.log(values);
  Logger.log(numRows);
  
  // Replace the original data with filtered recent data
  sheet.getRange('A2:H1001').clearContent(); //Clear values between rows 2-1001

  if (newData.length > 0) {
    Logger.log(newData);
    sheet.getRange(2, 1, newData.length, newData[0].length).setValues(newData);
  }
  if (oldData.length > 0) { //store old data to "HistoricalData"
    Logger.log(oldData);
    historicalSheet.getRange(historicalSheet.getLastRow() + 1, 1, oldData.length, oldData[0].length).setValues(oldData);
  } else { // if not return
    return;
  }

  //Update graph y-axes
  const graphTitle = ["Temperature (°C) for Last 30 Days", "Air Pressure (hPa) for Last 30 Days", "Battery Voltage (mV) for Last 30 Days", "Temperature (°C) for Last 7 Days", "Air Pressure (hPa) for Last 7 Days", "Battery Voltage (mV) for Last 7 Days"]
  graphRange = ["B3:B302","E3:E302", "G3:G302", "J3:J302", "M3:M302", "O3:O302"]
  changeYAxis(graphTitle, graphRange, "Data");
}
