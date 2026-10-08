import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "C:/Users/test/Desktop/projects/apollo_extension";
const outDir = `${root}/outputs/september_2026_login_audit`;
await fs.mkdir(outDir, { recursive: true });

const readJson = async (p) => JSON.parse(await fs.readFile(`${root}/${p}`, "utf8"));
const report = await readJson("scratch/september_audit_report.json");
const live = await readJson("config/apollo_live_account_report.json");
const mvJobs = await readJson("config/millionverifier_jobs.json");
const fsLedger = await readJson("config/freshsales_synced_batches.json");

const norm = (s) => String(s ?? "").trim().toLowerCase();
const reportByLogin = new Map(report.map((r) => [norm(r.login_email), r]));
const liveByLogin = new Map(live.accounts.map((r) => [norm(r.email), r]));

const mvByLogin = new Map();
for (const job of mvJobs) {
  const login = norm(job.login || "");
  if (!login.includes("@")) continue;
  const prev = mvByLogin.get(login) || { jobs: 0, sent: 0, good: 0, bad: 0, risky: 0, last: "" };
  prev.jobs += 1;
  prev.sent += Number(job.total_rows || 0);
  prev.good += Number(job.good_count || 0);
  prev.bad += Number(job.bad_count || 0);
  prev.risky += Number(job.risky_count || 0);
  prev.last = [prev.last, job.completed_at || job.created_at || ""].filter(Boolean).sort().pop() || "";
  mvByLogin.set(login, prev);
}

function resolveFsLogin(text) {
  const s = norm(text).replace(/[.@-]/g, "_");
  if (s.includes("rahul_nestack_co_in")) return "rahul@nestack.co.in";
  if (s.includes("rahul_nestacktechnology")) return "rahul@nestaktechnology.com";
  if (s.includes("rahul_nestack_tech")) return "rahul@nestack-tech.com";
  if (s.includes("vijay_raghavan_nestacktechnolog")) return "vijay.raghavan@nestacktechnologies.com";
  if (s.includes("vijay_raghavan_nestacktech")) return "vijay.raghavan@nestacktech.com";
  if (s.includes("vijay_raghavan_nestack_net")) return "vijay.raghavan@nestack.net";
  if (s.includes("vijay_raghavan_nestack_com")) return "vijay.raghavan@nestack.com";
  if (s.includes("vraghavan_nestacktech")) return "vraghavan@nestacktech.com";
  if (s.includes("vraghavan_nestack_com")) return "vraghavan@nestack.com";
  if (s.includes("vraghav_nestacktechnology")) return "vraghav@nestacktechnology.com";
  if (s.includes("rchandran_nestack_biz")) return "rchandran@nestack.biz";
  if (s.includes("rchandran_nestack_info")) return "rchandran@nestack.info";
  if (s.includes("madhava_reddy_nestack_tech")) return "madhava.reddy@nestack-tech.com";
  if (s.includes("madhava_reddy_nestacktech")) return "madhava.reddy@nestacktech.com";
  if (s.includes("recruiting_nestack_com")) return "recruiting@nestack.com";
  if (s.includes("vijay_nestacktech")) return "vijay@nestacktech.com";
  if (s.includes("jith_nestack_info")) return "jith@nestack.info";
  if (s.includes("abel_abraham_nestacktechnolog")) return "abel.abraham@nestacktechnologies.com";
  return "";
}

const fsByLogin = new Map();
for (const [key, entry] of Object.entries(fsLedger)) {
  const login = resolveFsLogin(`${key} ${entry.tag || ""} ${entry.file_stem || ""}`);
  if (!login) continue;
  const prev = fsByLogin.get(login) || { batches: 0, leads: 0, created: 0, updated: 0, tld: 0, last: "" };
  prev.batches += 1;
  prev.leads += Number(entry.total_leads || 0);
  prev.created += Number(entry.created || 0);
  prev.updated += Number(entry.updated || 0);
  prev.tld += Number(entry.tld_blocked || 0);
  prev.last = [prev.last, entry.synced_at || ""].filter(Boolean).sort().pop() || "";
  fsByLogin.set(login, prev);
}

const rows = report.map((r) => {
  const login = norm(r.login_email);
  const l = liveByLogin.get(login) || {};
  const mv = mvByLogin.get(login) || { jobs: 0, sent: 0, good: 0, bad: 0, risky: 0, last: "" };
  const fs = fsByLogin.get(login) || { batches: 0, leads: 0, created: 0, updated: 0, tld: 0, last: "" };
  const mvSent = mv.sent || r.mv_sent || 0;
  const mvGood = mv.good || r.mv_good || 0;
  return [
    login,
    r.db_saved || 0,
    r.db_enriched || 0,
    r.apollo_api_found || 0,
    r.created || 0,
    r.accounts_created || 0,
    r.domains_created || 0,
    r.updated || 0,
    mvSent,
    mvGood,
    mvSent ? (mvSent - mvGood) : 0,
    r.created ? (r.db_enriched - r.created) : r.db_enriched,
    mvSent ? (r.db_enriched - mvSent) : r.db_enriched,
    fs.created || 0,
    fs.updated || 0,
    fs.leads || 0,
    fs.last || "",
    l.status || "",
    l.expiry_ist || "",
    r.first_active || "",
    r.last_active || "",
    "Not tracked",
  ];
});

const headers = [
  "Login", "Complete data set / DB leads", "Amount enriched", "Apollo emails found",
  "Contacts created", "New accounts created", "New domains created", "Updated",
  "Sent for verification", "Passed verification", "Verification failed / risky",
  "Freshsales gap", "MillionVerifier gap", "Ledger-created total", "Ledger-updated total",
  "Ledger input total", "Latest sync", "Login status", "Expiry", "First active", "Last active",
  "Originating from festivals",
];

const wb = Workbook.create();
const summary = wb.worksheets.add("Summary");
const audit = wb.worksheets.add("Login Audit");
const sources = wb.worksheets.add("Source Register");
const notes = wb.worksheets.add("Definitions");
for (const s of [summary, audit, sources, notes]) { s.showGridLines = false; s.getUsedRange()?.clear?.({ applyTo: "all" }); }

const navy = "#17365D";
const blue = "#D9EAF7";
const light = "#F4F7FA";
const orange = "#FCE4D6";
const red = "#F4CCCC";
const green = "#E2F0D9";
const font = { name: "Arial", size: 10, color: "#1F2937" };

summary.getRange("A1:H1").merge();
summary.getRange("A1").values = [["September 2026 Login Import and Enrichment Audit"]];
summary.getRange("A1:H1").format = { font: { name: "Arial", size: 15, bold: true, color: "#FFFFFF" }, fill: navy, horizontalAlignment: "left", verticalAlignment: "center" };
summary.getRange("A2:H2").merge();
summary.getRange("A2").values = [["Scope: all 19 Apollo logins; September activity and current MillionVerifier/Freshsales ledger evidence"]];
summary.getRange("A2:H2").format = { font: { name: "Arial", size: 10, italic: true, color: "#44546A" } };
summary.getRange("A4:B9").values = [
  ["Metric", "Total"],
  ["Logins audited", rows.length],
  ["Complete data set / DB leads", null],
  ["Amount enriched", null],
  ["Contacts created", null],
  ["Updated", null],
];
summary.getRange("D4:E9").values = [
  ["Verification / CRM metric", "Total"],
  ["Sent for verification", null],
  ["Passed verification", null],
  ["New accounts created", null],
  ["New domains created", null],
  ["Latest ledger created", null],
];
summary.getRange("A4:B4").format = { fill: navy, font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" } };
summary.getRange("D4:E4").format = { fill: navy, font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" } };
summary.getRange("B6:B9").formulas = [["=SUM('Login Audit'!B6:B24)"], ["=SUM('Login Audit'!C6:C24)"], ["=SUM('Login Audit'!E6:E24)"], ["=SUM('Login Audit'!H6:H24)"]];
summary.getRange("E5:E9").formulas = [["=SUM('Login Audit'!I6:I24)"], ["=SUM('Login Audit'!J6:J24)"], ["=SUM('Login Audit'!F6:F24)"], ["=SUM('Login Audit'!G6:G24)"], ["=SUM('Login Audit'!N6:N24)"]];
summary.getRange("A4:E9").format.font = font;
summary.getRange("A4:E9").format.borders = { preset: "outside", style: "thin", color: "#B7C9D6" };
summary.getRange("B5:B9").format.numberFormat = "#,##0";
summary.getRange("E5:E9").format.numberFormat = "#,##0";
summary.getRange("A12:H15").values = [
  ["Interpretation", "", "", "", "", "", "", ""],
  ["Primary source: scratch/september_audit_report.json plus current MillionVerifier and Freshsales ledgers.", "", "", "", "", "", "", ""],
  ["Drive source: G:\\My Drive\\exported files\\september 2026 was not readable in this session; local synchronized exports were used.", "", "", "", "", "", "", ""],
  ["Festival-origin field: no festival/origin field exists in the available ledgers; shown as Not tracked.", "", "", "", "", "", "", ""],
];
summary.getRange("A12:H12").merge(); summary.getRange("A13:H13").merge(); summary.getRange("A14:H14").merge(); summary.getRange("A15:H15").merge();
summary.getRange("A12:H12").format = { fill: blue, font: { name: "Arial", size: 10, bold: true, color: navy } };
summary.getRange("A13:H15").format = { font: { name: "Arial", size: 10, color: "#44546A" }, wrapText: true };
summary.getRange("A1:H15").format.verticalAlignment = "center";
summary.getRange("A1:H15").format.autofitColumns();
summary.getRange("A1:H15").format.columnWidth = 22;
summary.getRange("A1:H1").format.rowHeight = 28;
summary.freezePanes.freezeRows(4);

audit.getRange("A1:V1").merge();
audit.getRange("A1").values = [["Login Audit Detail"]];
audit.getRange("A1:V1").format = { fill: navy, font: { name: "Arial", size: 15, bold: true, color: "#FFFFFF" } };
audit.getRange("A2:V2").merge(); audit.getRange("A2").values = [["Counts are sourced from the September audit report; latest ledger columns show current MV/FS records where available."]];
audit.getRange("A2:V2").format = { font: { name: "Arial", size: 10, italic: true, color: "#44546A" } };
audit.getRange("A5:V5").values = [headers];
audit.getRange("A5:V5").format = { fill: navy, font: { name: "Arial", size: 9, bold: true, color: "#FFFFFF" }, wrapText: true, horizontalAlignment: "center", verticalAlignment: "center" };
audit.getRange(`A6:V${rows.length + 5}`).values = rows;
audit.getRange(`A6:V${rows.length + 5}`).format = { font, verticalAlignment: "center" };
audit.getRange(`B6:Q${rows.length + 5}`).format.numberFormat = "#,##0";
audit.getRange(`A5:V${rows.length + 5}`).format.borders = { insideHorizontal: { style: "thin", color: "#D9E2F3" }, bottom: { style: "thin", color: "#B7C9D6" } };
audit.getRange(`K6:M${rows.length + 5}`).format.fill = orange;
audit.getRange(`V6:V${rows.length + 5}`).format.fill = light;
audit.tables.add(`A5:V${rows.length + 5}`, true, "LoginAuditTable");
audit.freezePanes.freezeRows(5); audit.freezePanes.freezeColumns(1);
audit.getRange("A:V").format.columnWidth = 16;
audit.getRange("A:A").format.columnWidth = 34;
audit.getRange("P:P").format.columnWidth = 24;
audit.getRange("Q:U").format.columnWidth = 18;
audit.getRange("V:V").format.columnWidth = 22;
audit.getRange("A5:V5").format.rowHeight = 42;

const sourceRows = [
  ["September audit", "scratch/september_audit_report.json", "19 login rows; September aggregate metrics", "Used"],
  ["Live account report", "config/apollo_live_account_report.json", "19 account statuses, credits, expiry", "Used"],
  ["MillionVerifier ledger", "config/millionverifier_jobs.json", "Verification jobs and Good/Bad/Risky counts", "Used"],
  ["Freshsales sync ledger", "config/freshsales_synced_batches.json", "Created, updated, TLD-blocked, sync timestamps", "Used"],
  ["Freshsales exports", "exports/freshsales_reports/*.csv", "Created-contact exports and audit files", "Used where present"],
  ["Requested Drive folder", "G:\\My Drive\\exported files\\september 2026", "User-referenced source folder", "Not readable in session"],
];
sources.getRange("A1:D1").merge(); sources.getRange("A1").values = [["Source Register"]];
sources.getRange("A1:D1").format = { fill: navy, font: { name: "Arial", size: 15, bold: true, color: "#FFFFFF" } };
sources.getRange("A3:D3").values = [["Source", "Location", "Content used", "Status"]];
sources.getRange("A3:D3").format = { fill: navy, font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" } };
sources.getRange(`A4:D${sourceRows.length + 3}`).values = sourceRows;
sources.getRange(`A4:D${sourceRows.length + 3}`).format = { font, wrapText: true, verticalAlignment: "center" };
sources.getRange("A:A").format.columnWidth = 24; sources.getRange("B:B").format.columnWidth = 58; sources.getRange("C:C").format.columnWidth = 48; sources.getRange("D:D").format.columnWidth = 20;
sources.getRange(`A3:D${sourceRows.length + 3}`).format.borders = { preset: "outside", style: "thin", color: "#B7C9D6" };
sources.freezePanes.freezeRows(3);

const defRows = [
  ["Metric", "Definition"],
  ["Complete data set / DB leads", "Leads saved in the Apollo/Enrich database for the login."],
  ["Amount enriched", "DB leads marked enriched in the September audit."],
  ["Contacts created", "Freshsales contacts created in the September audit output."],
  ["New accounts/domains", "Company accounts and unique corporate domains attributed to created contacts."],
  ["Updated", "Contacts updated in the September audit output."],
  ["Sent for verification", "Rows submitted to MillionVerifier."],
  ["Passed verification", "Rows marked Good by MillionVerifier."],
  ["Freshsales gap", "Amount enriched minus September Freshsales-created contacts."],
  ["MillionVerifier gap", "Amount enriched minus rows sent to MillionVerifier."],
  ["Ledger columns", "Current persisted Freshsales ledger totals; these can differ from the September snapshot."],
  ["Originating from festivals", "Not available in the source data; no festival/origin field was found."],
];
notes.getRange("A1:B1").merge(); notes.getRange("A1").values = [["Definitions and Audit Notes"]];
notes.getRange("A1:B1").format = { fill: navy, font: { name: "Arial", size: 15, bold: true, color: "#FFFFFF" } };
notes.getRange(`A3:B${defRows.length + 2}`).values = defRows;
notes.getRange("A3:B3").format = { fill: navy, font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" } };
notes.getRange(`A4:B${defRows.length + 2}`).format = { font, wrapText: true, verticalAlignment: "center" };
notes.getRange("A:A").format.columnWidth = 32; notes.getRange("B:B").format.columnWidth = 100;
notes.getRange(`A3:B${defRows.length + 2}`).format.borders = { preset: "outside", style: "thin", color: "#B7C9D6" };
notes.freezePanes.freezeRows(3);

wb.recalculate();
const preview = await wb.render({ sheetName: "Summary", autoCrop: "all", scale: 1, format: "png" });
await fs.writeFile(`${outDir}/summary.png`, new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(`${outDir}/september_2026_login_audit.xlsx`);

const check = await wb.inspect({ kind: "table", sheetId: "Login Audit", range: "A5:V10", include: "values,formulas", tableMaxRows: 6, tableMaxCols: 22, maxChars: 7000 });
console.log(check.ndjson);
console.log(`SAVED ${outDir}/september_2026_login_audit.xlsx`);
