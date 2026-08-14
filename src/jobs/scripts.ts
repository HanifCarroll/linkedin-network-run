import type { JobsSearchSpec } from "./types.ts";

/**
 * Plain playwriter script bodies for the jobs workflow. Each script prints one
 * JSON envelope (`{ok:true,data:{...}}`) as its final stdout line and never
 * throws for expected outcomes; the runner parses that line.
 */

/** LinkedIn jobs search page URL from a spec (f_TPR/f_WT are the standard filters). */
export function buildSearchUrl(spec: JobsSearchSpec): string {
  const url = new URL("https://www.linkedin.com/jobs/search");
  if (spec.keywords.trim().length > 0) url.searchParams.set("keywords", spec.keywords.trim());
  if (spec.location.trim().length > 0) url.searchParams.set("location", spec.location.trim());
  if (spec.postedWithinDays !== undefined) {
    const byDays: Readonly<Record<number, string>> = {
      1: "r86400",
      7: "r604800",
      14: "r1209600",
      30: "r2592000",
    };
    const token = byDays[spec.postedWithinDays];
    if (token === undefined) throw new TypeError("postedWithinDays must be 1, 7, 14, or 30");
    url.searchParams.set("f_TPR", token);
  }
  if (spec.remote === true) url.searchParams.set("f_WT", "2");
  return url.toString();
}

// Jobs scripts run on a page they own. context.pages() is shared across all
// sessions and agents (docs: "Pages are shared, state is not"), so grabbing
// any open tab can steal another agent's page mid-run (observed: goto
// interrupted by another navigation). The executor provides a fresh tracked
// `page` per call and state.jobsPage persists across calls, so prefer the
// carried page then the executor's own page; never newPage (extra tabs
// accumulate and overwhelm the relay) and never reuse shared tabs.
const PAGE_PICKUP = `
let p=null;{const stored=state.jobsPage;if(stored&&!stored.isClosed()){p=stored;}else{p=page;}if(!p||p.isClosed()){try{p=await context.newPage();}catch{const candidates=context.pages().filter((candidate)=>!candidate.isClosed());p=candidates.find((candidate)=>candidate.url()&&!candidate.url().startsWith("about:"))??candidates[0]??null;}}state.jobsPage=p;}if(!p||p.isClosed())throw new Error("NO_PAGE");`;

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
    const region = Array.from(document.querySelectorAll("h1,h2,h3,div,span"))
      .filter(el => /Meet the hiring team/i.test(el.innerText || ""))
      .sort((x, y) => (x.innerText || "").length - (y.innerText || "").length)
      .find(el => el.querySelectorAll("a[href*='/in/']").length > 0);
    if (region) links = Array.from(region.querySelectorAll("a[href*='/in/']"));
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
    let rp=null;
    try{rp=await context.newPage();}catch{const candidates=context.pages().filter((candidate)=>!candidate.isClosed());rp=candidates.find((candidate)=>candidate.url()&&!candidate.url().startsWith("about:"))??candidates[0]??null;}
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
  return {id,title:(TITLES[id]||view.title||"").trim(),company:view.company||"",location:view.location||"",postingUrl:"https://www.linkedin.com/jobs/view/"+id+"/",hiringTeam:team,hasHiringTeam:team.length>0};
};
const emptyRow=(id)=>({id,title:(TITLES[id]||"").trim(),company:"",location:"",postingUrl:"https://www.linkedin.com/jobs/view/"+id+"/",hiringTeam:[],hasHiringTeam:false});
`;

const CAPTURE_RESULT = `
try {
  p.removeAllListeners("response");
} catch {}
const byId=new Map();
const absorb=(included)=>{for(const e of included??[]){if(e.$type==="com.linkedin.voyager.dash.jobs.JobPosting"){const m=/fsd_jobPosting:(\\d+)$/.exec(e.entityUrn??"");if(m&&!byId.has(m[1]))byId.set(m[1],{id:m[1],title:e.title??""});}}};
const seenStarts=new Set();
let cards=null;
const onResponse=async(res)=>{
  if(!res.url().includes("voyagerJobsDashJobCards"))return;
  try{
    const b=await res.json();
    const els=b?.data?.elements;const pg=b?.data?.paging;
    if(Array.isArray(els)&&pg&&els.length>0){
      absorb(b?.included??[]);
      if(typeof pg.start==="number")seenStarts.add(pg.start);
      if(cards===null)cards={total:pg.total??0};
    }
  }catch{}
};
p.on("response",onResponse);
// The SPA soft-navigates after domcontentloaded, which can destroy the
// evaluation context and abort the goto; retry navigate+settle+capture until
// the cards land or tries are exhausted (same recovery as the network walk).
// A cached page renders results WITHOUT refiring the XHR; clicking a
// pagination control fires it (verified: "Page 2" fires the start:0 request),
// so the first click is the capture trigger, with a reload fallback.
const clickPage=async(n)=>{
  // Use Playwright's trusted click, not element.click(): the latter is
  // swallowed by React's synthetic events while the SPA is hydrating
  // (observed: 2 of 3 "Page 2" clicks did nothing).
  try{await p.locator("button[aria-label=\\"Page "+n+"\\"]").click({timeout:8000});return true;}catch{return false;}
};
const waitForStartGrowth=async(before)=>{
  // Paginated XHRs can arrive 15-20s after the click (observed), so allow a
  // generous window before concluding the page did not fire.
  for(let w=0;w<40&&seenStarts.size<=before;w+=1)await p.waitForTimeout(500);
};
// Click a page button repeatedly (every few seconds) until the XHR fires.
// Clicks during SPA hydration or transitions are silently dropped; the
// page's own controls are the only reliable trigger (XHR replay 403s).
const pollClick=async(n,before)=>{
  const deadline=Date.now()+12000;
  let lastClickAt=0;
  while(seenStarts.size<=before&&Date.now()<deadline){
    if(Date.now()-lastClickAt>4000){
      lastClickAt=Date.now();
      const clicked=await clickPage(n);
      if(!clicked)break;
    }
    await p.waitForTimeout(1000);
  }
  return seenStarts.size>before;
};
// The start:0 XHR fires unreliably — sometimes on goto, sometimes only after
// a reload or a pagination click. Fire every trigger in turn, bounded, and
// stop on the first that lands the cards.
const waitForCards=async()=>{for(let i=0;i<30&&cards===null;i+=1)await p.waitForTimeout(500);return cards!==null;};
let lastNavErr=null;let acquired=false;
try{
  await p.goto(CONFIG.searchUrl,{waitUntil:"commit",timeout:45000});
  await p.waitForTimeout(4000);
  acquired=await waitForCards();
  if(!acquired){try{await p.reload({waitUntil:"commit",timeout:45000});}catch{}await p.waitForTimeout(3000);acquired=await waitForCards();}
  if(!acquired){await pollClick(2,seenStarts.size);acquired=cards!==null;}
  if(!acquired){try{await p.reload({waitUntil:"commit",timeout:45000});}catch{}await p.waitForTimeout(3000);await pollClick(2,seenStarts.size);acquired=cards!==null;}
}catch(e){
  lastNavErr=e instanceof Error?e:new Error(String(e));
  const msg=String((e&&e.message)||e);
  if(/closed|context was destroyed|Execution context|navigation|Navigator|Timeout|fetch failed|ERR_ABORTED|net::/i.test(msg)){
    // Recreate the page if the user closed it or the relay dropped it.
    try{if(p.isClosed()){let rp=null;try{rp=await context.newPage();}catch{const candidates=context.pages().filter((candidate)=>!candidate.isClosed());rp=candidates.find((candidate)=>candidate.url()&&!candidate.url().startsWith("about:"))??candidates[0]??null;}if(rp){p=rp;state.jobsPage=p;}}await p.waitForTimeout(3000);}catch{}
  }
}
try{p.removeListener("response",onResponse);}catch{}
if(!acquired)throw new Error("JOBS_CARDS_XHR_NOT_CAPTURED"+(lastNavErr?": "+String((lastNavErr&&lastNavErr.message)||lastNavErr):""));
// Paginate with the page's own pagination controls: replaying the XHR 403s
// (CSRF check failed) and the SPA strips &start from the URL. Clicking the
// page buttons fires the voyagerJobsDashJobCards requests (observed sequence:
// buttons 2,3,4,5 -> starts 0,25,50,75); the listener absorbs every response.
// Data still comes only from the XHR. The acquisition already clicked the
// page-2 button, so this loop starts at page 3. Clicks during SPA hydration
// or transitions are silently dropped, so re-click every few seconds until
// the page's start arrives or the deadline passes.
// The acquisition already clicked the page-2 button (fires the start:0
// request). Paginate onward with the remaining page buttons — the visible
// set varies across renders, so collect whatever pages the SPA offers and
// re-run the search to converge on the target (found teams are skipped).
// Collect as many pages as the user asked. Already-seen postings are filtered
// out at the end, so paginating past exhausted pages is how a re-run finds
// fresh ones. Deeper pages are render-variable; the loop stops early if a
// page button stops firing its XHR.
const neededStarts=CONFIG.pages;
// Settle before paginating so the results list and footer are fully live
// (the probe that paged reliably waited ~10s before its first click).
await p.waitForTimeout(8000);
for(let n=3;seenStarts.size<neededStarts&&byId.size<CONFIG.jobCountTarget;n+=1){
  const grew=await pollClick(n,seenStarts.size);
  if(!grew)break;
}
const ids=[...byId.keys()].filter((id)=>!CONFIG.skipIds.includes(id)).slice(0,CONFIG.hiringTeamLimit);
const batch={byId:Object.fromEntries([...byId.entries()].map(([id,entry])=>[id,entry.title])),ids,results:{},pagesCollected:seenStarts.size,cardsTotal:cards.total};
state.jobsBatch=batch;
const poolJobs=ids.map((id)=>{const entry=byId.get(id);return {id,title:entry?entry.title:""};});
console.log(JSON.stringify({ok:true,data:{pool:ids.length,pagesCollected:seenStarts.size,cardsTotal:cards.total,jobs:poolJobs}}));
`;

const ENRICH_RESULT = `
const batch=state.jobsBatch;
const pending=batch.ids.filter((id)=>batch.results[id]===undefined).slice(0,CONFIG.batchSize);
if(CONFIG.workers<=1){
  // Sequential on the pickup page: every extra attached tab multiplies the
  // relay's CDP event fanout (observed: 3 parallel workers wedge the relay
  // mid-run, producing all-empty extractions), so one tab is the robust path.
  for(const id of pending){
    try{batch.results[id]=await enrichOne(p,id);}catch{batch.results[id]=emptyRow(id);}
  }
}else{
  // Fixed worker pages, reused across calls (no accumulation). Opt-in: the
  // fanout of parallel tabs has proven to wedge the relay on long runs.
  let workers=state.jobsWorkers;
  if(!Array.isArray(workers))workers=[];
  for(let w=workers.length;w<CONFIG.workers;w+=1){
    let wp=null;
    try{wp=await context.newPage();}catch{const candidates=context.pages().filter((candidate)=>!candidate.isClosed());wp=candidates.find((candidate)=>candidate.url()&&!candidate.url().startsWith("about:"))??candidates[0]??null;}
    if(!wp)break;
    workers.push(wp);
  }
  state.jobsWorkers=workers;
  const workerJobs=Array.from({length:workers.length},()=>[]);
  pending.forEach((id,i)=>{workerJobs[i%workers.length].push(id);});
  await Promise.all(workerJobs.map((jobs,i)=>jobs.length?Promise.all(jobs.map(async(id)=>{try{batch.results[id]=await enrichOne(workers[i],id);}catch{batch.results[id]=emptyRow(id);}})):Promise.resolve()));
}
const enriched=Object.keys(batch.results).length;
const remaining=batch.ids.length-enriched;
const found=Object.values(batch.results).filter((r)=>r.hasHiringTeam).length;
const pendingSet=new Set(pending);
const completed=batch.ids.filter((id)=>pendingSet.has(id)&&batch.results[id]!==undefined).map((id)=>batch.results[id]);
console.log(JSON.stringify({ok:true,data:{enriched,remaining,found,completed}}));
`;

const FINISH_RESULT = `
const batch=state.jobsBatch;
const jobs=batch.ids.map((id)=>batch.results[id]).filter((r)=>r!==undefined);
console.log(JSON.stringify({ok:true,data:{jobs,pagesCollected:batch.pagesCollected,cardsTotal:batch.cardsTotal}}));
`;

const ENRICH_POOL_RESULT = `
const results=[];
for(const job of CONFIG.jobs){
  try{results.push(await enrichOne(p,job.id));}catch{results.push(emptyRow(job.id));}
}
console.log(JSON.stringify({ok:true,data:{completed:results}}));
`;

/**
 * Build the search + enrichment script. Collects the `voyagerJobsDashJobCards`
 * XHR for the search, paginates by replaying with `start` bumped, then loads
 * each job's direct view to extract company, location, and hiring team.
 */
/**
 * Phase 1: capture the `voyagerJobsDashJobCards` XHR for the search, paginate
 * by replaying with `start` bumped, and persist the id pool in
 * state.jobsBatch. Runs in one call (well under the 300s relay cap).
 */
export function buildCaptureScript(config: {
  readonly searchUrl: string;
  readonly pages: number;
  readonly hiringTeamLimit: number;
  readonly skipIds?: readonly string[];
}): { readonly script: string; readonly timeoutMs: number } {
  if (!Number.isSafeInteger(config.pages) || config.pages < 1 || config.pages > 10)
    throw new TypeError("pages must be 1..10");
  if (
    !Number.isSafeInteger(config.hiringTeamLimit) ||
    config.hiringTeamLimit < 1 ||
    config.hiringTeamLimit > 200
  )
    throw new TypeError("hiringTeamLimit must be 1..200");
  const cfg = {
    searchUrl: config.searchUrl,
    pages: config.pages,
    hiringTeamLimit: config.hiringTeamLimit,
    skipIds: config.skipIds ?? [],
    jobCountTarget: config.pages * 25,
  };
  // Top-level awaits only: the playwriter executor does not await an async IIFE.
  const source = `const CONFIG=${JSON.stringify(cfg)};\n${PAGE_PICKUP}\n${CAPTURE_RESULT}`;
  return { script: source, timeoutMs: 420_000 };
}

/**
 * Phase 2: enrich the next batch of job views (hiring team, company,
 * location), appending to state.jobsBatch.results. Repeat until the caller's
 * target is met or `remaining` is 0.
 */
export function buildEnrichScript(config: {
  readonly batchSize: number;
  readonly workers?: number;
}): { readonly script: string; readonly timeoutMs: number } {
  if (!Number.isSafeInteger(config.batchSize) || config.batchSize < 1 || config.batchSize > 60)
    throw new TypeError("batchSize must be 1..60");
  const workers = config.workers ?? 3;
  if (!Number.isSafeInteger(workers) || workers < 1 || workers > 5)
    throw new TypeError("workers must be 1..5");
  const cfg = { batchSize: config.batchSize, workers };
  const source = `const CONFIG=${JSON.stringify(cfg)};const TITLES=state.jobsBatch.byId;const VIEW_EXTRACT=${EXTRACT_VIEW};\n${PAGE_PICKUP}\n${VIEW_ATTEMPT}\n${ENRICH_RESULT}`;
  return { script: source, timeoutMs: 420_000 };
}

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
/**
 * Close accumulated blank automation tabs (about:blank / empty URL) before a
 * drain. MV3 service workers get reclaimed under tab-buildup memory pressure,
 * so trimming them between reconnect cycles reduces the drop frequency.
 */
export function buildCleanupTabsScript(): { readonly script: string; readonly timeoutMs: number } {
  const source = `let closed=0;try{const pages=context.pages().filter((p)=>{try{return !p.isClosed();}catch{return false;}});for(const pg of pages){try{const url=await pg.url().catch(()=>"");if((url===""||url.startsWith("about:"))&&pg!==page){await pg.close().catch(()=>{});closed+=1;}}catch{}}}catch{}console.log(JSON.stringify({ok:true,data:{closed}}));`;
  return { script: source, timeoutMs: 60_000 };
}

/** Phase 3: assemble the full result envelope from state.jobsBatch. */
export function buildFinishScript(): { readonly script: string; readonly timeoutMs: number } {
  const source = `${PAGE_PICKUP}\n${FINISH_RESULT}`;
  return { script: source, timeoutMs: 120_000 };
}

export const SEND_TIMEOUT_MS = 90_000;

const SEND_RESULT = `
const out={jobId:CONFIG.jobId,memberName:CONFIG.memberName,status:"failed",detail:"",confirmed:false};
try{
  await p.goto(CONFIG.profileUrl,{waitUntil:"domcontentloaded",timeout:60000});
  await p.waitForTimeout(3000);
  let msg=null;
  const buttons=p.locator("button");
  const n=await buttons.count();
  for(let i=0;i<n;i+=1){
    const b=buttons.nth(i);
    if(!(await b.isVisible().catch(()=>false)))continue;
    const text=((await b.textContent().catch(()=>""))||"").replace(/\\s+/g," ").trim();
    const aria=((await b.getAttribute("aria-label").catch(()=>null))||"").replace(/\\s+/g," ").trim();
    if(/^Message$/i.test(text)||(/^Message$/i.test(aria)&&!/more/i.test(aria))){msg=b;break;}
  }
  if(!msg){out.status="no_message_button";out.detail="no Message CTA on profile";console.log(JSON.stringify({ok:true,data:out}));return;}
  await msg.click();
  await p.waitForTimeout(2500);
  const box=p.locator("div[role='textbox'][contenteditable='true']").last();
  if(await box.count()===0){out.status="no_composer";out.detail="messenger composer not found";console.log(JSON.stringify({ok:true,data:out}));return;}
  await box.click();
  try{await box.fill(CONFIG.message);}catch{await p.keyboard.type(CONFIG.message,{delay:2});}
  await p.waitForTimeout(800);
  let sendBtn=null;
  const sbuttons=p.locator("button");
  const sn=await sbuttons.count();
  for(let i=0;i<sn;i+=1){
    const b=sbuttons.nth(i);
    if(!(await b.isVisible().catch(()=>false)))continue;
    const text=((await b.textContent().catch(()=>""))||"").replace(/\\s+/g," ").trim();
    const aria=((await b.getAttribute("aria-label").catch(()=>null))||"").replace(/\\s+/g," ").trim();
    if(/^Send$/i.test(text)||/^Send$/i.test(aria)){sendBtn=b;break;}
  }
  if(!sendBtn){out.status="no_send_button";out.detail="composer open but no Send CTA";console.log(JSON.stringify({ok:true,data:out}));return;}
  await sendBtn.click();
  await p.waitForTimeout(3500);
  const needle=CONFIG.message.slice(0,60);
  const bodyText=await p.evaluate(()=>document.body.innerText).catch(()=>"");
  out.confirmed=bodyText.includes(needle);
  out.status="sent";
  out.detail=out.confirmed?"message visible in thread":"Send clicked; confirmation unverified";
  console.log(JSON.stringify({ok:true,data:out}));
}catch(e){
  out.status="failed";
  out.detail=String((e&&e.message)||e).slice(0,300);
  console.log(JSON.stringify({ok:true,data:out}));
}`;

/** Build the message-send script for one hiring team member profile. */
export function buildSendScript(config: {
  readonly jobId: string;
  readonly memberName: string;
  readonly profileUrl: string;
  readonly message: string;
}): { readonly script: string; readonly timeoutMs: number } {
  if (!/^https:\/\/www\.linkedin\.com\/in\//.test(config.profileUrl))
    throw new TypeError("profileUrl must be a linkedin.com/in/ profile URL");
  if (config.message.trim().length === 0) throw new TypeError("message must not be empty");
  // Top-level awaits only: the playwriter executor does not await an async IIFE.
  const source = `const CONFIG=${JSON.stringify(config)};\n${PAGE_PICKUP}\n${SEND_RESULT}`;
  return { script: source, timeoutMs: SEND_TIMEOUT_MS };
}
