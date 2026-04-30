console.log("loaded by:", process.versions.electron ? "ELECTRON v" + process.versions.electron : "NODE only");
const e = require("electron");
console.log("typeof require('electron'):", typeof e);
console.log("keys:", typeof e === 'object' ? Object.keys(e).slice(0, 8) : 'n/a');
console.log("app:", typeof e.app);
process.exit(0);
