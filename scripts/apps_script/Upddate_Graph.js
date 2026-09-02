function changeYAxis(graphTitle, graphRange, sheet) {
  const charts = graphSheet.getCharts();
  const dataSheet = ss.getSheetByName(sheet);

  charts.forEach(function(chart) {
    for (let i = 0; i < graphTitle.length; i++) {
      if(chart.getOptions().get("title") == graphTitle[i]) {
        // Define the range containing the data you want to use for min/max calculation
        const dataRange = dataSheet.getRange(graphRange[i]); 
        const dataValues = dataRange.getValues();

        // Convert 2D array to 1D array
        const flatValues = dataValues.flat().filter(isNumber);
        Logger.log(flatValues);
        let minVal = Math.min(...flatValues);
        let maxVal = Math.max(...flatValues);
  
        // Add a buffer to min/max for better visualization
        const buffer = (maxVal - minVal) * 0.1; // 10% buffer
        minVal = minVal - buffer;
        maxVal = maxVal + buffer;

        minVal = Math.floor(minVal);
        maxVal = Math.ceil(maxVal);

        Logger.log(minVal);
        Logger.log(maxVal);

        // Modify the chart's y-axis
        chart = chart.modify()
          .setOption('vAxis.viewWindow.min', minVal)
          .setOption('vAxis.viewWindow.max', maxVal)
          .build();
        graphSheet.updateChart(chart);
        break;
      }
    }
  });
}

function isNumber(value) {
  return typeof value === "number";
}
