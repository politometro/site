const fs = require("fs");
const path = require("path");

try {
  let dataDir = path.join(__dirname, "data");
  if (!fs.existsSync(dataDir)) {
    dataDir = path.join(__dirname, "..", "data");
  }
  console.log("dataDir resolved to:", dataDir);
  console.log("dataDir exists:", fs.existsSync(dataDir));
  
  if (fs.existsSync(dataDir)) {
    const files = fs.readdirSync(dataDir);
    console.log("Files in dataDir:", files);
    
    // Find the Orçamentos folder
    const budgetsFolder = files.find(f => f.toLowerCase().includes("orçamento"));
    console.log("Budgets folder found:", budgetsFolder);
    
    if (budgetsFolder) {
      const budgetsDir = path.join(dataDir, budgetsFolder);
      console.log("budgetsDir resolved to:", budgetsDir);
      console.log("budgetsDir exists:", fs.existsSync(budgetsDir));
      if (fs.existsSync(budgetsDir)) {
        const budgetFiles = fs.readdirSync(budgetsDir);
        console.log("Files inside budgetsDir count:", budgetFiles.length);
        console.log("PDF files inside budgetsDir count:", budgetFiles.filter(f => f.toLowerCase().endsWith(".pdf")).length);
      }
    }
  }
} catch (err) {
  console.error("Error:", err);
}
