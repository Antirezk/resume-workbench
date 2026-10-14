import fs from 'node:fs/promises';
import path from 'node:path';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const root = path.resolve(import.meta.dirname, '..');
const output = path.join(root, 'output', 'verification');
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(output, '中文简历整理_演示.xlsx')));
const checks = await workbook.inspect({kind:'table',range:'候选人总表!A1:H4',tableMaxRows:4,tableMaxCols:8,maxChars:2500});
console.log(checks.ndjson);
const ranges = [
  ['候选人总表','A1:H5'], ['工作经历','A1:H4'], ['教育经历','A1:G4'],
  ['项目经历','A1:H4'], ['原文证据','A1:D4'], ['未成功与重复文件','A1:C1'],
  ['实习经历','A1:H4'], ['其他任职经历','A1:H1'], ['证书奖项与学术成果','A1:H4'],
  ['语言能力','A1:E4'], ['作品集与个人主页','A1:D1'], ['候选人总表','M1:P5']
];
for (let i = 0; i < ranges.length; i++) {
  const [sheetName, range] = ranges[i];
  const preview = await workbook.render({sheetName,range,scale:1.5,format:'png'});
  await fs.writeFile(path.join(output, `sheet-${i + 1}.png`), new Uint8Array(await preview.arrayBuffer()));
}
console.log('Eleven sheets imported and rendered successfully.');
