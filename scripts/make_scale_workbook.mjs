// Optional fixture authoring tool (@oai/artifact-tool). The VM uses the checked-in XLSX.
import fs from "node:fs/promises";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const [out, png] = process.argv.slice(2);
if (!out) throw new Error("usage: node make_scale_workbook.mjs OUTPUT.xlsx [PREVIEW.png]");
const wb = Workbook.create(), sh = wb.worksheets.add("Scale");
const rows = [["记录", "接口", "边界代号", "类型", "例化（公式）", "拍数", "时钟源信号", "复位源信号",
  "源代号", "门控", "路线一", "路线二", "小计", "原始名字"]];
function add(id, signal, pos, mod, inst, step, ck="NA", rst="NA", source="NA", gate="NA") {
  rows.push([id,signal,pos,mod,null,step,ck,rst,source,gate,"NA","NA","NA",inst]);
}
add("parent","q","ROOT","qa_rs","LEFT",96,"NA","NA","NA","假门控");
for (let i=0;i<96;i++) {
  const tag=i.toString().padStart(3,"0"), tile="T"+tag;
  add(tag+":tx","link",tile,"qa_tx","TX",1,"root_clock","root_reset","SOURCE");
  add(tag+":skip","NA","UNMAPPED_SKIP","unused","SKIP",97);
  add(tag+":rx","link",tile,"qa_rx","RX",1);
  add(tag+":pipe","q",tile,"qa_rs","PIPE",16,"NA","NA","NA","假门控");
  add(tag+":own","q","D"+tag,"qa_rs","OWN",1,"NA","NA","NA","假门控");
}
sh.getRange("A1:N"+rows.length).values=rows;
sh.getRange("E2:E"+rows.length).formulas=rows.slice(1).map((_,i)=>["=N"+(i+2)]);
for (let i=0;i<96;i++) {
  const n=3+5*i;
  sh.getRange("M"+n+":M"+(n+2)).merge();
  sh.getRange("M"+n).formulas=[["=SUM(F"+n+",F"+(n+2)+")"]];
}
sh.getRange("A1:N1").format.fill="#19384B";
sh.getRange("A1:N1").format.font={bold:true,color:"#FFFFFF"};
sh.getRange("A1:N1").format.rowHeight=26;
sh.getRange("A:N").format.columnWidth=16;
sh.getRange("F:F").format.columnWidth=9;
sh.freezePanes.freezeRows(1);
wb.recalculate();
for (const i of [0,47,95]) {
  const n=3+i*5;
  if(sh.getRange("M"+n).values[0][0]!==2 || sh.getRange("E"+n).values[0][0]!=="TX")
    throw new Error("cache calculation failed: "+n);
}
if (png) {
  const preview=await wb.render({sheetName:"Scale",range:"A1:N8",scale:1,format:"png"});
  await fs.writeFile(png,new Uint8Array(await preview.arrayBuffer()));
}
await (await SpreadsheetFile.exportXlsx(wb)).save(out);
console.log("Wrote 481 data rows / 481 selector formulas / 96 merged total formulas");
