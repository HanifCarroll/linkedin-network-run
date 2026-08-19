/**
 * Plain playwriter script bodies for the jobs workflow. Each script prints one
 * JSON envelope (`{ok:true,data:{...}}`) as its final stdout line and never
 * throws for expected outcomes; the runner parses that line.
 */

// Jobs scripts run on one carried page. context.pages() is shared across all
// sessions and agents, and the executor's `page` is just context.pages()[0]
// (reused, never a fresh tab). The only tab creator is context.newPage(), so
// scripts must never call it: extra tabs accumulate and overwhelm the relay,
// which surfaces as "Extension not connected" drops. Prefer the carried page
// (state.jobsPage) then the executor's `page`; if both are closed, reuse an
// existing open tab; never create one.
const PAGE_PICKUP = `
let p=null;{const stored=state.jobsPage;if(stored&&!stored.isClosed()){p=stored;}else{p=page;}if(!p||p.isClosed()){const candidates=context.pages().filter((candidate)=>!candidate.isClosed());p=candidates.find((candidate)=>candidate.url()&&!candidate.url().startsWith("about:"))??candidates[0]??null;}state.jobsPage=p;}if(!p||p.isClosed())throw new Error("NO_PAGE");`;

export function buildCleanupTabsScript(): { readonly script: string; readonly timeoutMs: number } {
  const source = `let closed=0;state.jobsPage=null;try{const pages=context.pages().filter((p)=>{try{return !p.isClosed();}catch{return false;}});for(const pg of pages){try{const url=pg.url();if((url===""||url.startsWith("about:"))&&pg!==page){try{await pg.close();closed+=1;}catch{}}}catch{}}}catch{}console.log(JSON.stringify({ok:true,data:{closed}}));`;
  return { script: source, timeoutMs: 60_000 };
}

const CHECK_LIVENESS_RESULT = `
const results=[];
for(const job of CONFIG.jobs){
  try{
    await p.goto("https://www.linkedin.com/jobs/view/"+job.id+"/",{waitUntil:"commit",timeout:25000});
    const t=String(await p.title().catch(()=>""));
    const parts=t.split(" | ").map((s)=>s.trim()).filter(Boolean);
    const first=parts[0]??"";const last=parts[parts.length-1]??"";
    if(last!=="LinkedIn"){results.push({id:job.id,error:"unexpected title: "+t});continue;}
    if(first==="Jobs"){results.push({id:job.id,live:false});continue;}
    let body="";
    for(let w=0;w<6;w+=1){body=String(await p.evaluate(()=>document.body?document.body.innerText:"").catch(()=>""));if(body.length>200)break;await p.waitForTimeout(500);}
    if(/No longer accepting applications/i.test(body)){results.push({id:job.id,live:false});continue;}
    if(parts.length>=3){results.push({id:job.id,live:true});continue;}
    results.push({id:job.id,error:"ambiguous title: "+t});
  }catch(e){results.push({id:job.id,error:String((e&&e.message)||e)});}
}
console.log(JSON.stringify({ok:true,data:{checked:results}}));
`;

/**
 * reads the title and body: a live posting has "Title | Company | LinkedIn",
 * a removed one has the generic "Jobs | LinkedIn", and a closed one keeps its
 * title but swaps the apply control for a "No longer accepting applications"
 * banner. Far cheaper than enrich (no scroll or hiring-team extraction) —
 * used by `jobs check`.
 */
export function buildCheckLivenessScript(config: {
  readonly jobs: readonly { readonly id: string }[];
}): { readonly script: string; readonly timeoutMs: number } {
  if (config.jobs.length === 0) throw new TypeError("jobs must be non-empty");
  const cfg = { jobs: config.jobs };
  const source = `const CONFIG=${JSON.stringify(cfg)};\n${PAGE_PICKUP}\n${CHECK_LIVENESS_RESULT}`;
  // ~35s per job (25s goto + title read + margin), kept under the 300s relay cap.
  const timeoutMs = Math.min(config.jobs.length * 35_000, 280_000);
  return { script: source, timeoutMs };
}

export const SEND_TIMEOUT_MS = 290_000;

const SEND_PICKUP = `
let p=page;\nif(!p||p.isClosed()){\n  const candidates=context.pages().filter((candidate)=>!candidate.isClosed());\n  p=candidates.find((candidate)=>candidate.url()&&!candidate.url().startsWith(\"about:\"))??candidates[0]??null;\n}\nif(!p||p.isClosed())throw new Error(\"NO_PAGE\");
`;

// Send flow: a direct Message CTA on the profile when present; otherwise read
// the member's profileUrn from the profile's static /messaging/compose anchor,
// navigate the SAME page to the Sales Navigator lead URL, and message from the
// lead page's in-place InMail composer (subject + message + Send). No new tabs
// are ever created: site-opened tabs are invisible to the playwriter bridge, so
// the overflow More -> "Message [name]" path (which opens a Sales Nav tab) is
// never taken; More is opened only to re-render the static anchors, never to
// send. SEND_PICKUP binds the executor's live `page` (pages[0]), not the
// persisted state.jobsPage: a stale carried page can be a dropped tab whose
// evaluate() reads empty, which previously surfaced as lead_page_not_loaded.
const SEND_RESULT = `
const out={jobId:CONFIG.jobId,memberName:CONFIG.memberName,status:"failed",detail:"",confirmed:false};
const clean=(s)=>String(s??"").replace(/\\s+/g," ").trim();
// Bind the matching button immediately: nth locators re-resolve across renders.
const findButtonHandle=async(pg,re)=>{
  const list=pg.locator("button");
  const n=await list.count();
  for(let i=0;i<n;i+=1){
    const b=list.nth(i);
    if(!(await b.isVisible().catch(()=>false)))continue;
    const text=clean(await b.textContent().catch(()=>""));
    const aria=clean(await b.getAttribute("aria-label").catch(()=>null));
    if(re.test(text)||re.test(aria))return b.elementHandle().catch(()=>null);
  }
  return null;
};
const bind=async(loc)=>loc.elementHandle().catch(()=>null);
try{
  await p.goto(CONFIG.profileUrl,{waitUntil:"domcontentloaded",timeout:30000});
  await p.waitForTimeout(3000);
  let messageHandle=await findButtonHandle(p,/^Message$/i);
  let usingInMail=false;
  if(!messageHandle){
    // No direct Message CTA. The compose anchor's profileUrn is the lead id;
    // message from the Sales Nav lead page's in-place InMail composer.
    const firstName=clean(CONFIG.memberName.split(/\\s+/)[0]||"");
    const findUrn=async()=>{
      const anchors=p.locator("a[href*='/messaging/compose/?profileUrn=']");
      const n=await anchors.count();
      for(let i=0;i<n;i+=1){
        const a=anchors.nth(i);
        const t=clean(await a.textContent().catch(()=>""));
        if(!t.toLowerCase().startsWith(("message "+firstName).toLowerCase()))continue;
        const href=clean(await a.getAttribute("href").catch(()=>null));
        const m=/profileUrn=([^&]+)/.exec(href);
        if(m)return decodeURIComponent(m[1]);
      }
      return "";
    };
    let urn="";
    // Poll ~15s for the static compose anchor.
    for(let w=0;w<15&&!urn;w+=1){
      urn=await findUrn();
      if(urn)break;
      await p.waitForTimeout(1000);
    }
    if(!urn){
      // Last resort: open the More overflow once so its anchors render. This
      // is a trusted elementHandle.click, never the dropdown's Message item.
      const moreHandle=await findButtonHandle(p,/^(More|More actions)$/i);
      if(moreHandle){
        try{await moreHandle.click({timeout:5000});}catch{}
        await p.waitForTimeout(1500);
        urn=await findUrn();
      }
    }
    if(!urn){out.status="no_message_button";out.detail="no Message CTA on profile and no compose anchor for "+CONFIG.memberName;console.log(JSON.stringify({ok:true,data:out}));return;}
    const leadUrl="https://www.linkedin.com/sales/lead/"+urn+",name,s9qk";
    // A successful goto can still land on an empty/error document, so retry
    // the same-page navigation once unless the verified lead Message CTA lands.
    let leadLoaded=false;
    for(let attempt=0;attempt<2&&!messageHandle;attempt+=1){
      try{await p.goto(leadUrl,{waitUntil:"domcontentloaded",timeout:30000});}
      catch(e){
        const detail=String((e&&e.message)||e);
        const transient=/navigation|Timeout|ERR_ABORTED|ERR_CONNECTION_CLOSED|net::|context was destroyed|Execution context/i.test(detail);
        if(attempt===0&&transient){await p.waitForTimeout(2000);continue;}
        out.status="failed";out.detail=detail;console.log(JSON.stringify({ok:true,data:out}));return;
      }
      for(let w=0;w<25&&!messageHandle;w+=1){
        const body=await p.evaluate(()=>document.body?document.body.innerText:"").catch(()=>"");
        if(/Sales Navigator Lead Page/i.test(body)){
          leadLoaded=true;
          messageHandle=await findButtonHandle(p,/^Message$/i);
        }
        if(!messageHandle)await p.waitForTimeout(1000);
      }
    }
    if(!messageHandle){
      out.status=leadLoaded?"no_message_button":"lead_page_not_loaded";
      out.detail=leadLoaded?"Sales Nav lead page has no Message button":"Sales Nav lead page did not load";
      console.log(JSON.stringify({ok:true,data:out}));return;
    }
    usingInMail=true;
  }
  // Open the composer in place (profile messenger or Sales Nav InMail).
  try{await messageHandle.click({timeout:10000});}catch(e){out.status="failed";out.detail=String((e&&e.message)||e);console.log(JSON.stringify({ok:true,data:out}));return;}
  let box=null;
  const boxSelector=usingInMail
    ? "textarea[aria-label='Type your message here or create draft']"
    : "div[role='textbox'][contenteditable='true']";
  for(let w=0;w<30&&!box;w+=1){
    const b=p.locator(boxSelector).last();
    if(await b.count()>0&&await b.isVisible().catch(()=>false)){box=await bind(b);break;}
    await p.waitForTimeout(1000);
  }
  if(!box){
    out.status="no_composer";
    out.detail="messenger composer not found ("+String(await p.url().catch(()=>""))+")";
    console.log(JSON.stringify({ok:true,data:out}));return;
  }
  if(usingInMail&&CONFIG.subject){
    const subj=p.locator("input[aria-label='Subject (required)']").first();
    try{await subj.fill(CONFIG.subject);}catch(e){out.status="no_composer";out.detail="InMail subject field not found: "+String((e&&e.message)||e);console.log(JSON.stringify({ok:true,data:out}));return;}
  }
  try{await box.fill(CONFIG.message);}catch{await box.click({timeout:5000}).catch(()=>{});await p.keyboard.type(CONFIG.message,{delay:2});}
  await p.waitForTimeout(800);
  const sendHandle=await findButtonHandle(p,/^Send$/i);
  if(!sendHandle){out.status="no_send_button";out.detail="composer open but no Send CTA";console.log(JSON.stringify({ok:true,data:out}));return;}
  try{await sendHandle.click({timeout:10000});}catch(e){out.status="failed";out.detail=String((e&&e.message)||e);console.log(JSON.stringify({ok:true,data:out}));return;}
  const needle=CONFIG.message.slice(0,60);
  for(let w=0;w<15&&!out.confirmed;w+=1){
    await p.waitForTimeout(1000);
    const bodyText=await p.evaluate(()=>document.body?document.body.innerText:"").catch(()=>"");
    out.confirmed=bodyText.includes(needle);
  }
  // Only report sent when the message is actually visible in the thread.
  out.status=out.confirmed?"sent":"failed";
  out.detail=out.confirmed?"message visible in thread":"Send clicked; confirmation unverified";
  console.log(JSON.stringify({ok:true,data:out}));
}catch(e){
  out.status="failed";
  out.detail=String((e&&e.message)||e);
  console.log(JSON.stringify({ok:true,data:out}));
}
`;

/** Build the message-send script for one hiring team member profile. */
export function buildSendScript(config: {
  readonly jobId: string;
  readonly memberName: string;
  readonly profileUrl: string;
  readonly subject: string;
  readonly message: string;
}): { readonly script: string; readonly timeoutMs: number } {
  if (!/^https:\/\/www\.linkedin\.com\/in\//.test(config.profileUrl))
    throw new TypeError("profileUrl must be a linkedin.com/in/ profile URL");
  if (config.message.trim().length === 0) throw new TypeError("message must not be empty");
  // Top-level awaits only: the playwriter executor does not await an async IIFE.
  const source = `const CONFIG=${JSON.stringify(config)};\n${SEND_PICKUP}\n${SEND_RESULT}`;
  return { script: source, timeoutMs: SEND_TIMEOUT_MS };
}
