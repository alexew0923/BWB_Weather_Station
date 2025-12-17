function changeYAxis(graphTitle, graphRange, sheet) {
  const dataSheet = ss.getSheetByName(sheet);
  let charts = graphSheet.getCharts();
  charts.forEach(function(chart){
    for (let i = 0; i < graphTitle.length; i++) {
      if(chart.getOptions().get("title") === graphTitle[i]) {
        // Define the range containing the data you want to use for min/max calculation
        const dataRange = dataSheet.getRange(graphRange[i]); 
        const dataValues = dataRange.getValues();
        // Flatten the array and remove empty cells to find min and max
        const flatValues = dataValues.map(row => row[0]).filter(String);
        let minVal = Math.min(...flatValues);
        if (graphTitle[i] === "Snow Depth (cm)" || graphTitle[i] === "Snow Depth (cm) for Last 30 Days" || graphTitle[i] === "Snow Depth (cm) for Last 7 Days") {
          minVal = 0; //always set the minimum value of snow depth to 0 for a better visualization
        }
        let maxVal = Math.max(...flatValues);

        // Optional: Add a buffer to min/max for better visualization
        const buffer = (maxVal - minVal) * 0.1; // 10% buffer
        minVal = minVal - buffer;
        if (graphTitle[i] === "Snow Depth (cm)" || graphTitle[i] === "Snow Depth (cm) for Last 30 Days" || graphTitle[i] === "Snow Depth (cm) for Last 7 Days") {
          minVal = 0; //always set the minimum value of snow depth to 0 for a better visualization
        }
        maxVal = maxVal + buffer;

        minVal = Math.floor(minVal);
        maxVal = Math.ceil(maxVal);

        Logger.log(minVal);
        Logger.log(maxVal);

        // Modify the chart's vertical axis
        chart = chart.modify()
          .setOption('vAxes.0.viewWindow.min', minVal)
          .setOption('vAxes.0.viewWindow.max', maxVal)
          .build();

        graphSheet.updateChart(chart);
      }
    }
  });
}
