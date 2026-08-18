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

const EXTRACT_VIEW = `
() => {
  const docTitle = document.title || "";
  const parts = docTitle.split(" | ");
  const last = parts.length > 0 ? parts[parts.length - 1] : "";
  if (last === "LinkedIn") parts.pop();
  const company = parts.length >= 2 ? parts[parts.length - 1] : "";
  const title = parts.length >= 2 ? parts.slice(0, -1).join(" | ") : (parts[0] ?? "");
  const lines = document.body.innerText.split("\\n").map(l => l.trim()).filter(Boolean);
  // Anchor the location line to the title row: "United States · 2 days ago ·
  // Over 100 applicants". Scanning from the start hits hidden nav text.
  const titleAnchor = title.length > 8 ? title.slice(0, 30) : "";
  const titleIdx = titleAnchor ? lines.findIndex(l => l.includes(titleAnchor)) : -1;
  const scanFrom = titleIdx >= 0 ? titleIdx : 0;
  const postedLine = lines.slice(scanFrom, scanFrom + 8).find(l => /·\\s*\\d+\\s*(minute|hour|day|week|month)s?\\s+ago|·\\s*(today|yesterday)/i.test(l));
  const location = postedLine ? (postedLine.split("·")[0] ?? "").trim() : "";
  // Hiring-team members are profile links labeled "Job poster" in their own
  // text. The "Meet the hiring team" heading is not a clean single element
  // across builds, so fall back to the smallest region containing that text.
  const team = [];
  let links = Array.from(document.querySelectorAll("a[href*='/in/']")).filter(a => /Job poster/i.test(a.innerText || ""));
  if (links.length === 0) {
    // Anchor to the exact heading, not any element merely containing the
    // phrase (which would match <body> and the whole rail). Walk up a few
    // levels to the smallest container holding the member cards, and stop
    // before any container that also wraps a different rail, so a fellow
    // job-seeker's card from "People also viewed" is never captured as a
    // hiring contact.
    const heading = Array.from(document.querySelectorAll("h1,h2,h3,h4,div,span,section"))
      .filter((el) => (el.innerText || "").trim() === "Meet the hiring team")
      .sort((x, y) => (x.innerText || "").length - (y.innerText || "").length)[0];
    if (heading) {
      let container = heading.parentElement;
      for (let up = 0; up < 4 && container; up += 1) {
        const text = container.innerText || "";
        if (/\b(People also viewed|People you may know|Similar jobs|About the job|About the company|Set alert|Explore more)\b/i.test(text)) break;
        const found = container.querySelectorAll("a[href*='/in/']");
        if (found.length > 0) { links = Array.from(found); break; }
        container = container.parentElement;
      }
    }
  }
  for (const a of links) {
    const lines = (a.innerText || "").split("\\n").map(l => l.trim()).filter(Boolean);
    const name = lines[0] || "";
    if (!name || team.some(m => m.name === name)) continue;
    const href = a.getAttribute("href") || "";
    const degreeLine = lines.find(l => /^•?\\s*([123](?:st|nd|rd))\\s*$/i.test(l));
    const degree = degreeLine ? (/[123](?:st|nd|rd)/i.exec(degreeLine) || [])[0] ?? "" : "";
    const headline = lines.slice(1).filter(l => l !== degreeLine && !/^(Job poster|Message|Follow|Connect|Show more|View profile)$/i.test(l)).join(" ");
    team.push({ name, profileUrl: href.startsWith("http") ? href : "https://www.linkedin.com" + href, degree, headline });
  }
  return { title, company, location, team };
}`;

// Full posting-page detail. LinkedIn job views ship no JSON-LD and no stable
// CSS classes (build-hashed), so this parses the SSR'd innerText with content
// anchors: the meta line for location/posted/applicants, exact enum lines for
// workplace/employment/apply, and the "About the job".."Benefits found in job
// post" range for the description.
const DETAIL_EXTRACT = `
() => {
  const lines = document.body.innerText.split("\\n").map(l => l.trim()).filter(Boolean);
  const pick = (re) => lines.find(l => re.test(l)) || "";
  const metaLine = pick(/·\\s*(Just now|\\d+\\s+(minute|hour|day|week|month)s?\\s+ago|today|yesterday)/i);
  const metaParts = metaLine.split("·").map(s => s.trim()).filter(Boolean);
  const postedAt = metaParts.find(p => /ago|today|yesterday|just now/i.test(p)) || "";
  const applicantCount = metaParts.find(p => /applicant/i.test(p)) || "";
  const workplaceType = pick(/^(On-site|Remote|Hybrid)$/i);
  const employmentType = pick(/^(Full-time|Part-time|Contract|Temporary|Internship|Other)$/i);
  const applyMethod = pick(/^(Easy Apply|Apply)$/i);
  const promoLine = pick(/Promoted/i);
  const promoted = /Promoted/i.test(promoLine);
  const activelyReviewing = /Actively reviewing/i.test(promoLine);
  const aboutIdx = lines.findIndex(l => /^About the job$/i.test(l));
  let endIdx = lines.length;
  const endMarkers = [/^Benefits found in job post$/i, /^Set alert for similar jobs$/i, /^Similar jobs$/i];
  if (aboutIdx >= 0) {
    for (let i = aboutIdx + 1; i < lines.length; i += 1) {
      if (endMarkers.some(re => re.test(lines[i]))) { endIdx = i; break; }
    }
  }
  const description = aboutIdx >= 0
    ? lines.slice(aboutIdx + 1, endIdx).filter(l => !/^(… more|Show more|See more)$/i.test(l)).join("\\n").trim()
    : "";
  const benIdx = lines.findIndex(l => /^Benefits found in job post$/i.test(l));
  let benefits = [];
  if (benIdx >= 0) {
    let j = benIdx + 1;
    while (j < lines.length && !/^Set alert|^Similar jobs/i.test(lines[j])) j += 1;
    benefits = lines.slice(benIdx + 1, j).flatMap(l => l.split(",")).map(s => s.trim()).filter(s => s.length > 0);
  }
  return { postedAt, applicantCount, workplaceType, employmentType, applyMethod, promoted, activelyReviewing, description, benefits, dead: /^Jobs\\s*\\|/i.test(document.title) };
}`;

// The playwriter CLI (<=0.4.0) sends execute requests through Node/Undici
// fetch, whose fixed 300s response-header timeout kills any call longer than
// ~300s with "fetch failed" (remorses/playwriter#74). Search therefore runs as
// phased calls — capture, then small enrich batches, then finish — with
// progress persisted in state.jobsBatch between calls. The ceiling lifts once
// we upgrade to the playwriter release that fixes #74.

const VIEW_ATTEMPT = `
const viewAttempt=async(wp,jobId)=>{
  // The user may close our tab mid-run (docs: always check before using).
  if(wp.isClosed()){
    const candidates=context.pages().filter((candidate)=>!candidate.isClosed());
    const rp=candidates.find((candidate)=>candidate.url()&&!candidate.url().startsWith("about:"))??candidates[0]??null;
    if(!rp)throw new Error("NO_PAGE");
    wp=rp;
  }
  await wp.goto("https://www.linkedin.com/jobs/view/"+jobId+"/",{waitUntil:"commit",timeout:25000});
  await wp.waitForTimeout(800);
  // Wait for the SSR document title to land (app-owned readiness signal) —
  // title is set at document load and differs from the previous page.
  try{
    const previousTitle=await wp.title().catch(()=>"");
    for(let w=0;w<8;w+=1){const t=await wp.title().catch(()=>"");if(t&&t!==previousTitle&&t.includes("LinkedIn"))break;await wp.waitForTimeout(400);}
  }catch{}
  await wp.waitForTimeout(1500);
  // The hiring-team section is lazy-rendered below the fold; scroll down
  // incrementally to trigger it (a single jump is unreliable).
  try{for(let s=0;s<5;s+=1){await wp.evaluate(()=>window.scrollBy(0,900));await wp.waitForTimeout(350);}await wp.waitForTimeout(1000);}catch{}
  return wp.evaluate(VIEW_EXTRACT);
};
const enrichOne=async(wp,id)=>{
  let view=null;let lastViewErr=null;
  for(let attempt=0;attempt<3&&view===null;attempt+=1){
    try{view=await viewAttempt(wp,id);}catch(e){lastViewErr=e;const msg=String((e&&e.message)||e);if(!/context was destroyed|Execution context|navigation|Navigator|Timeout|fetch failed|ERR_ABORTED|net::/i.test(msg))throw e;await wp.waitForTimeout(2500);}
  }
  if(view===null)throw lastViewErr??new Error("VIEW_UNREACHABLE");
  const team=(view.team??[]).map(t=>({name:t.name,profileUrl:t.profileUrl,degree:t.degree||"",headline:t.headline||""}));
  return {id,title:(TITLES[id]||view.title||"").trim(),company:view.company||"",location:view.location||"",postingUrl:"https://www.linkedin.com/jobs/view/"+id+"/",hiringTeam:team,hasHiringTeam:team.length>0,dead:!view.company&&/^jobs$/i.test(String(view.title||"").trim())};
};
const emptyRow=(id)=>({id,title:(TITLES[id]||"").trim(),company:"",location:"",postingUrl:"https://www.linkedin.com/jobs/view/"+id+"/",hiringTeam:[],hasHiringTeam:false,dead:false});
`;

const ENRICH_POOL_RESULT = `
const results=[];
for(const job of CONFIG.jobs){
  try{results.push(await enrichOne(p,job.id));}catch{results.push(emptyRow(job.id));}
}
console.log(JSON.stringify({ok:true,data:{completed:results}}));
`;

/**
 * Enrich a caller-supplied batch of captured jobs (id + title), returning the
 * enriched rows. Used by `jobs enrich` to drain the DB-backed captured pool —
 * no capture phase and no state.jobsBatch dependency.
 */
export function buildEnrichPoolScript(config: {
  readonly jobs: readonly { readonly id: string; readonly title: string }[];
}): { readonly script: string; readonly timeoutMs: number } {
  if (config.jobs.length === 0) throw new TypeError("jobs must be non-empty");
  const cfg = { jobs: config.jobs };
  const source = `const CONFIG=${JSON.stringify(cfg)};const TITLES=Object.fromEntries(CONFIG.jobs.map((j)=>[j.id,j.title]));const VIEW_EXTRACT=${EXTRACT_VIEW};\n${PAGE_PICKUP}\n${VIEW_ATTEMPT}\n${ENRICH_POOL_RESULT}`;
  return { script: source, timeoutMs: 420_000 };
}

const DETAIL_RESULT = `
const detailOne=async(wp,jobId)=>{
  if(wp.isClosed()){
    const candidates=context.pages().filter((candidate)=>!candidate.isClosed());
    const rp=candidates.find((candidate)=>candidate.url()&&!candidate.url().startsWith("about:"))??candidates[0]??null;
    if(!rp)throw new Error("NO_PAGE");
    wp=rp;
  }
  await wp.goto("https://www.linkedin.com/jobs/view/"+jobId+"/",{waitUntil:"commit",timeout:25000});
  // The description SSR-hydrates into innerText a few seconds after commit.
  // Poll for it rather than a fixed wait: the main document is not scrollable
  // (scrollY stays 0 on LinkedIn job views), so scroll-to-trigger was a no-op,
  // and a single early read can miss a slow hydration.
  let d=null;
  for(let attempt=0;attempt<6;attempt+=1){
    try{d=await wp.evaluate(DETAIL_EXTRACT);}
    catch(e){
      const msg=String((e&&e.message)||e);
      if(!/context was destroyed|Execution context|navigation|Navigator/i.test(msg))throw e;
      d=null;
    }
    if(d!==null&&(d.dead===true||(d.description||"").length>0))break;
    await wp.waitForTimeout(2500);
  }
  if(d===null)throw new Error("DETAIL_UNREACHABLE");
  return {id:jobId,...d};
};
const results=[];
for(const job of CONFIG.jobs){
  try{results.push(await detailOne(p,job.id));}
  catch(e){results.push({id:job.id,postedAt:"",applicantCount:"",workplaceType:"",employmentType:"",applyMethod:"",promoted:false,activelyReviewing:false,description:"",benefits:[],dead:false,error:String((e&&e.message)||e)});}
}
console.log(JSON.stringify({ok:true,data:{completed:results}}));
`;

/**
 * Phase 2b: pull the full posting-page detail (description + structured
 * header fields) for a batch of already-collected jobs. No hiring-team
 * extraction — that already happened in enrich.
 */
export function buildDetailScript(config: { readonly jobs: readonly { readonly id: string }[] }): {
  readonly script: string;
  readonly timeoutMs: number;
} {
  if (config.jobs.length === 0) throw new TypeError("jobs must be non-empty");
  const cfg = { jobs: config.jobs };
  const source = `const CONFIG=${JSON.stringify(cfg)};const DETAIL_EXTRACT=${DETAIL_EXTRACT};\n${PAGE_PICKUP}\n${DETAIL_RESULT}`;
  // ~35s per job (25s goto + settle + extract), kept under the 300s relay cap.
  const timeoutMs = Math.min(config.jobs.length * 35_000, 280_000);
  return { script: source, timeoutMs };
}
/**
 * Close accumulated blank automation tabs (about:blank / empty URL) before a
 * drain and clear the persisted carried page. Two problems this fixes:
 *  - MV3 service workers get reclaimed under tab-buildup memory pressure, so
 *    trimming tabs between reconnect cycles reduces the drop frequency.
 *  - `state.jobsPage` is a stale handle after an extension drop: `isClosed()`
 *    still reports false, so PAGE_PICKUP keeps navigating the dead page and
 *    every enrich batch comes back empty. Nulling it forces PAGE_PICKUP onto
 *    the executor's fresh `page`.
 */
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
