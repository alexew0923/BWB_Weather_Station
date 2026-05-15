function rebuildDailyReliabilityAudit() {
  const lock = LockService.getScriptLock();

  // Prevent two audit runs from overlapping.
  if (!lock.tryLock(30000)) {
    throw new Error("Could not get script lock. Another audit run may still be running.");
  }

  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();

    const rawSheetName = "HistoricalData";
    const auditSheetName = "Daily_Reliability_Audit";

    const rawSheet = ss.getSheetByName(rawSheetName);
    if (!rawSheet) {
      throw new Error("Missing sheet: " + rawSheetName);
    }

    let auditSheet = ss.getSheetByName(auditSheetName);
    if (!auditSheet) {
      auditSheet = ss.insertSheet(auditSheetName);
    }

    const lastRow = rawSheet.getLastRow();
    const lastCol = rawSheet.getLastColumn();

    if (lastRow < 2) {
      throw new Error("HistoricalData has no data rows.");
    }

    // Read only the real used range from HistoricalData.
    const values = rawSheet.getRange(1, 1, lastRow, lastCol).getValues();

    const headers = values[0].map(String);

    const COL = {
      date: headers.indexOf("Date"),
      temp: headers.indexOf("Temperature"),
      humidity: headers.indexOf("Humidity"),
      soil: headers.indexOf("Soil Moisture"),
      pressure: headers.indexOf("Air Pressure"),
      rain: headers.indexOf("Rain Value"),
      battery: headers.indexOf("Battery Voltage"),
      count: headers.indexOf("Count")
    };

    if (COL.date === -1) throw new Error("Missing Date column.");
    if (COL.temp === -1) throw new Error("Missing Temperature column.");
    if (COL.humidity === -1) throw new Error("Missing Humidity column.");
    if (COL.soil === -1) throw new Error("Missing Soil Moisture column.");
    if (COL.pressure === -1) throw new Error("Missing Air Pressure column.");
    if (COL.rain === -1) throw new Error("Missing Rain Value column.");
    if (COL.battery === -1) throw new Error("Missing Battery Voltage column.");
    if (COL.count === -1) throw new Error("Missing Count column.");

    const EXPECTED_ROWS_PER_DAY = 288;
    const timezone = Session.getScriptTimeZone();

    const daily = {};

    for (let i = 1; i < values.length; i++) {
      const row = values[i];
      const timestamp = row[COL.date];

      if (!timestamp) continue;

      const dateObj = new Date(timestamp);
      if (isNaN(dateObj.getTime())) continue;

      const dateKey = Utilities.formatDate(dateObj, timezone, "yyyy-MM-dd");

      if (!daily[dateKey]) {
        daily[dateKey] = {
          rowsReceived: 0,
          tempPresent: 0,
          humidityPresent: 0,
          soilPresent: 0,
          pressurePresent: 0,
          rainPresent: 0,
          batteryPresent: 0,
          countPresent: 0
        };
      }

      // Row count measures whether the system transmitted data to the sheet.
      daily[dateKey].rowsReceived++;

      // Sensor counts measure whether each sensor value exists inside received rows.
      if (isPresent(row[COL.temp])) daily[dateKey].tempPresent++;
      if (isPresent(row[COL.humidity])) daily[dateKey].humidityPresent++;
      if (isPresent(row[COL.soil])) daily[dateKey].soilPresent++;
      if (isPresent(row[COL.pressure])) daily[dateKey].pressurePresent++;
      if (isPresent(row[COL.rain])) daily[dateKey].rainPresent++;
      if (isPresent(row[COL.battery])) daily[dateKey].batteryPresent++;
      if (isPresent(row[COL.count])) daily[dateKey].countPresent++;
    }

    const dateKeys = Object.keys(daily).sort();

    if (dateKeys.length === 0) {
      throw new Error("No valid dated rows found in HistoricalData.");
    }

    const startDate = parseDateKey(dateKeys[0]);
    const endDate = parseDateKey(dateKeys[dateKeys.length - 1]);

    const output = [];

    output.push([
      "Date",
      "Expected Rows",
      "Rows Received",
      "Row Completeness",
      "Temperature Present",
      "Humidity Present",
      "Soil Moisture Present",
      "Air Pressure Present",
      "Rain Value Present",
      "Battery Voltage Present",
      "Count Present",
      "Temperature Completeness",
      "Humidity Completeness",
      "Air Pressure Completeness",
      "Rain Completeness",
      "Battery Completeness",
      "Failure Class"
    ]);

    // Include every calendar day, even if there were zero rows.
    for (let d = new Date(startDate); d <= endDate; d.setDate(d.getDate() + 1)) {
      const dateKey = Utilities.formatDate(d, timezone, "yyyy-MM-dd");

      const stats = daily[dateKey] || {
        rowsReceived: 0,
        tempPresent: 0,
        humidityPresent: 0,
        soilPresent: 0,
        pressurePresent: 0,
        rainPresent: 0,
        batteryPresent: 0,
        countPresent: 0
      };

      const rowsReceived = stats.rowsReceived;

      // Cap at 100% because more than 288 rows is not "better than perfect."
      const rowCompleteness = Math.min(rowsReceived / EXPECTED_ROWS_PER_DAY, 1);

      const tempCompleteness = rowsReceived ? stats.tempPresent / rowsReceived : 0;
      const humidityCompleteness = rowsReceived ? stats.humidityPresent / rowsReceived : 0;
      const pressureCompleteness = rowsReceived ? stats.pressurePresent / rowsReceived : 0;
      const rainCompleteness = rowsReceived ? stats.rainPresent / rowsReceived : 0;
      const batteryCompleteness = rowsReceived ? stats.batteryPresent / rowsReceived : 0;

      const failureClass = classifyFailure(
        rowsReceived,
        rowCompleteness,
        tempCompleteness,
        humidityCompleteness,
        pressureCompleteness,
        rainCompleteness
      );

      output.push([
        dateKey,
        EXPECTED_ROWS_PER_DAY,
        rowsReceived,
        rowCompleteness,
        stats.tempPresent,
        stats.humidityPresent,
        stats.soilPresent,
        stats.pressurePresent,
        stats.rainPresent,
        stats.batteryPresent,
        stats.countPresent,
        tempCompleteness,
        humidityCompleteness,
        pressureCompleteness,
        rainCompleteness,
        batteryCompleteness,
        failureClass
      ]);
    }

    // Clear only the exact output area. This is faster than clearing old/stale ranges.
    auditSheet.getRange(1, 1, output.length, output[0].length).clearContent();

    // Write audit output in one batch.
    auditSheet.getRange(1, 1, output.length, output[0].length).setValues(output);

    
  // Timestamp so you can verify exactly when the trigger ran.
  const lastUpdated = Utilities.formatDate(
    new Date(),
    Session.getScriptTimeZone(),
    "yyyy-MM-dd HH:mm:ss"
  );

  auditSheet.getRange("S1").setValue("Audit Last Updated");
  auditSheet.getRange("S2").setValue(lastUpdated);

  } finally {
    lock.releaseLock();
  }
}

function isPresent(value) {
  return value !== "" && value !== null && value !== undefined;
}

function parseDateKey(dateKey) {
  const parts = dateKey.split("-");
  return new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
}

function classifyFailure(
  rowsReceived,
  rowCompleteness,
  tempCompleteness,
  humidityCompleteness,
  pressureCompleteness,
  rainCompleteness
) {
  if (rowsReceived === 0) {
    return "Full outage";
  }

  if (rowCompleteness < 0.25) {
    return "Severe transmission loss";
  }

  if (rowCompleteness < 0.75) {
    return "Partial transmission loss";
  }

  const worstCoreSensor = Math.min(
    tempCompleteness,
    humidityCompleteness,
    pressureCompleteness,
    rainCompleteness
  );

  if (rowCompleteness >= 0.75 && worstCoreSensor < 0.75) {
    return "Sensor-level issue";
  }

  if (rowCompleteness >= 0.95) {
    return "Good transmission day";
  }

  return "Minor transmission loss";
}


// Run this manually only if you want to reformat the audit sheet.
// Do not attach this function to a trigger.
function formatDailyReliabilityAudit() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const auditSheet = ss.getSheetByName("Daily_Reliability_Audit");

  if (!auditSheet) {
    throw new Error("Missing sheet: Daily_Reliability_Audit");
  }

  const lastRow = auditSheet.getLastRow();
  const lastCol = auditSheet.getLastColumn();

  if (lastRow < 2) return;

  auditSheet.getRange(1, 1, 1, lastCol).setFontWeight("bold");
  auditSheet.getRange(2, 4, lastRow - 1, 1).setNumberFormat("0.00%");
  auditSheet.getRange(2, 12, lastRow - 1, 5).setNumberFormat("0.00%");
  auditSheet.autoResizeColumns(1, lastCol);
}
