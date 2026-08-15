import { PlaywriterClient } from "./src/playwriter/client.ts";
const client = new PlaywriterClient({
  invocationRoot: "/tmp/pw-probe",
  createInvocationId: () => "pw_probe_01",
});
const source = [
  `let p=page;`,
  `const seen=[];`,
  `page.on("response",async(res)=>{try{seen.push({u:res.url(),s:res.status()});}catch(e){seen.push({err:String(e)});}});`,
  `page.on("request",async(req)=>{try{seen.push({req:req.url()});}catch(e){seen.push({reqerr:String(e)});}});`,
  `await p.goto("https://www.linkedin.com/sales/search/people?savedSearchId=1980870185",{waitUntil:"domcontentloaded"});`,
  `await p.waitForTimeout(8000);`,
  `const filtered=seen.filter(x=>/salesApi|leadSearch|sales-api/i.test(JSON.stringify(x)));`,
  `return JSON.stringify({total:seen.length,filtered:filtered.slice(0,5)});`,
].join("\n");
const inv = await client.invoke({ sessionId: 15, source });
console.log("OUTCOME", inv.receipt.outcome);
console.log("DATA", inv.receipt.result?.data);
console.log("BLOCKER", JSON.stringify(inv.receipt.blocker));
