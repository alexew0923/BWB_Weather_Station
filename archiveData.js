function archiveOldSensorData() {
    const data = sheet.getDataRange().getValues();
  
    const date = new Date();
    // Create a new Date object with only year, month, day
    const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    
    const newData = []; // Start with headers
    const oldData = [];
    const values = new Array(6);
    let numRows = [0, 0, 0, 0, 0, 0];
  
    for (let i = 1; i < data.length; i++) {
      const row = data[i];
      Logger.log(row);
      const timestamp = new Date(row[0]);
      
      if (timestamp < dateOnly) {
        oldData.push(row);
        for (let j = 0; j < 6; j++) {
          if (row[j+1] !== "") {
            if (numRows[j] == 0) {
              values[j] = row[j+1];
            } else {
              values[j] += row[j+1];
            }
            numRows[j] ++;
          }
        }
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
    
    dateOnly.setDate(dateOnly.getDate() - 1); 
    archiveSheet.appendRow([dateOnly, ...values]);
    numberSheet.appendRow([dateOnly, ...numRows]);
  
    Logger.log(values);
    Logger.log(numRows);
    
    if (data.length < 2) return;
    // Replace the original data with filtered recent data
    const lastRow = sheet.getLastRow() - 1;
    sheet.deleteRows(2, lastRow); // start from row 2, delete remaining rows
  
    if (newData.length > 0) {
      Logger.log(newData);
      sheet.getRange(2, 1, newData.length, newData[0].length).setValues(newData);
    }
    if (oldData.length > 0) {
      Logger.log(oldData);
      historicalSheet.getRange(historicalSheet.getLastRow() + 1, 1, oldData.length, oldData[0].length).setValues(oldData);
    } else {
      return;
    }
  
    const graphTitle = ["Temperature (°C) for Last 30 Days", "Air Pressure (hPa) for Last 30 Days", "Snow Depth (cm) for Last 30 Days","Temperature (°C) for Last 7 Days", "Air Pressure (hPa) for Last 7 Days", "Snow Depth (cm) for Last 7 Days"]
    const graphRange = ["B3:B","E3:E","G3:G", "L3:L", "O3:O", "Q3:Q"]
    changeYAxis(graphTitle, graphRange, "Data");
  }