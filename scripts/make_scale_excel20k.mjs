// Optional authoring dependency only. VM tests read the prebuilt real XLSX.
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const output=process.argv[2];
if(!output) throw new Error("usage: node make_scale_excel20k.mjs OUTPUT.xlsx");
const wb=Workbook.create(), sh=wb.worksheets.add("Scale");
sh.getRange("A1:N1").values=[["记录","接口","边界代号","模块","例化（公式）","拍数","时钟","复位","源","门控","路线一","路线二","小计","名字原文"]];
for(let start=0;start<20000;start+=1000) {
  const rows=[];
  for(let i=start;i<start+1000;i++) {
    const skip=i%40===39;
    rows.push([i+1,skip?"NA":"link","ROOT","qa_rs",null,skip?97:1,"NA","NA","NA","假门控","NA","NA",null,"PIPE_"+String(i).padStart(5,"0")]);
  }
  sh.getRange("A"+(start+2)+":N"+(start+1001)).values=rows;
  sh.getRange("E"+(start+2)+":E"+(start+1001)).formulas=rows.map((_,j)=>["=N"+(start+j+2)]);
}
for(let g=0;g<500;g++) {
  const n=2+g*40;
  sh.getRange("M"+n+":M"+(n+39)).merge();
  sh.getRange("M"+n).formulas=[["=SUM(F"+n+":F"+(n+38)+")"]];
}
sh.getRange("A1:N1").format.fill="#19384B";
sh.getRange("A1:N1").format.font={bold:true,color:"#FFFFFF"};
sh.getRange("A:N").format.columnWidth=16;
sh.getRange("F:F").format.columnWidth=9;
sh.freezePanes.freezeRows(1);
wb.recalculate();
for(const n of [2,10002,19962]) {
  if(sh.getRange("M"+n).values[0][0]!==39 || sh.getRange("E"+n).values[0][0]!=="PIPE_"+String(n-2).padStart(5,"0"))
    throw new Error("calculated cell mismatch at "+n);
}
await (await SpreadsheetFile.exportXlsx(wb)).save(output);
console.log("XLSX 20000 data rows, 20500 cached formulas, 500 independent merges");
