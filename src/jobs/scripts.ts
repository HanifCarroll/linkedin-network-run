import type { JobsSearchSpec } from "./types.ts";

/**
 * Plain playwriter script bodies for the jobs workflow. Each script prints one
 * JSON envelope (`{ok:true,data:{...}}`) as its final stdout line and never
 * throws for expected outcomes; the runner parses that line.
 */

const SEARCH_TIMEOUT_MS = 300_000;

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

// Jobs scripts own a dedicated page. Never navigate the user's open LinkedIn
// tabs: that interrupts their work and races scheduled automations (observed:
// goto interrupted by another navigation). Prefer a carried page, then any
// blank tab, then a new page; newPage can time out while the relay is busy
// with other automation, so it is the last resort.
const PAGE_PICKUP = `
let p=null;{const stored=state.jobsPage;if(stored&&!stored.isClosed()){p=stored;}else{const candidates=context.pages().filter((candidate)=>!candidate.isClosed());p=candidates.find((candidate)=>candidate.url()==="about:blank")??null;if(!p){try{p=await context.newPage();}catch{throw new Error("JOBS_BROWSER_BUSY: no blank tab and newPage timed out; retry when other automation has finished");}}}state.jobsPage=p;}`;

const EXTRACT_VIEW = `
() => {
  const docTitle = document.title || "";
  const parts = docTitle.split(" | ");
  const last = parts.length > 0 ? parts[parts.length - 1] : "";
  if (last === "LinkedIn") parts.pop();
  const company = parts.length >= 2 ? parts[parts.length - 1] : "";
  const title = parts.length >= 2 ? parts.slice(0, -1).join(" | ") : (parts[0] ?? "");
  const lines = document.body.innerText.split("\\n").map(l => l.trim()).filter(Boolean);
  const postedIdx = lines.findIndex(l => l.includes("·") && /ago|applicant|today|hour/i.test(l));
  const location = postedIdx >= 0 ? (lines[postedIdx].split("·")[0] ?? "").trim() : "";
  const heading = Array.from(document.querySelectorAll("h1,h2,h3,div,span")).find(el => /^Meet the hiring team$/i.test((el.innerText || "").trim()));
  const team = [];
  let teamText = "";
  if (heading) {
    let container = heading;
    for (let i = 0; i < 4 && container; i += 1) container = container.parentElement;
    if (container) {
      teamText = container.innerText.trim();
      for (const a of Array.from(container.querySelectorAll("a[href*='/in/']"))) {
        const name = (a.innerText || "").trim().replace(/\\s+/g, " ");
        if (!name || team.some(m => m.name === name)) continue;
        const href = a.getAttribute("href") || "";
        team.push({ name, profileUrl: href.startsWith("http") ? href : "https://www.linkedin.com" + href, degree: "", headline: "" });
      }
      const tlines = teamText.split("\\n").map(l => l.trim()).filter(Boolean);
      for (const m of team) {
        const idx = tlines.findIndex(l => l === m.name);
        if (idx >= 0) {
          const next = tlines[idx + 1] ?? "";
          const dm = /^\\s*•?\\s*([123](?:st|nd|rd))/.exec(next);
          if (dm) m.degree = dm[1];
        }
        const head = [];
        for (let j = idx + (idx >= 0 ? 2 : 1); j < tlines.length; j += 1) {
          const l = tlines[j];
          if (team.some(t => t.name === l)) break;
          if (/^\\s*•?\\s*[123](?:st|nd|rd)/.test(l)) break;
          if (/^(Follow|Message|Connect|Recently hired|Actively reviewing|Promoted by|Save)/.test(l)) break;
          head.push(l);
        }
        m.headline = head.join(" ");
      }
    }
  }
  return { title, company, location, team, teamText };
}`;

const SEARCH_RESULT = `
const result={jobs:[],pagesCollected:0,cardsTotal:0};
try {
  p.removeAllListeners("response");
} catch {}
let cards=null;
const onResponse=async(res)=>{
  if(cards)return;
  if(!res.url().includes("voyagerJobsDashJobCards"))return;
  try{const b=await res.json();const els=b?.data?.elements;const pg=b?.data?.paging;if(Array.isArray(els)&&pg&&els.length>0)cards={elements:els,paging:pg,included:b?.included??[],requestUrl:res.url()};}catch{}
};
p.on("response",onResponse);
await p.goto(CONFIG.searchUrl,{waitUntil:"domcontentloaded",timeout:60000});
for(let i=0;i<40&&cards===null;i+=1)await p.waitForTimeout(500);
if(cards===null){try{await p.reload({waitUntil:"domcontentloaded",timeout:60000});}catch{}for(let i=0;i<40&&cards===null;i+=1)await p.waitForTimeout(500);}
if(cards===null)throw new Error("JOBS_CARDS_XHR_NOT_CAPTURED");
result.pagesCollected=1;
result.cardsTotal=cards.paging.total??0;
const byId=new Map();
const absorb=(included)=>{for(const e of included??[]){if(e.$type==="com.linkedin.voyager.dash.jobs.JobPosting"){const m=/fsd_jobPosting:(\\d+)$/.exec(e.entityUrn??"");if(m&&!byId.has(m[1]))byId.set(m[1],{id:m[1],title:e.title??""});}}};
absorb(cards.included);
for(let pg=1;pg<CONFIG.pages&&byId.size<CONFIG.jobCountTarget;pg+=1){
  const u=new URL(cards.requestUrl);
  const start=String(pg*25);
  u.searchParams.set("start",start);
  const q=u.searchParams.get("query")??"";
  if(/start:\\d+/.test(q))u.searchParams.set("query",q.replace(/start:\\d+/,"start:"+start));
  try{const r=await fetch(u.toString(),{credentials:"include",signal:AbortSignal.timeout(30000)});const b=await r.json();const els=b?.data?.elements??[];if(els.length===0)break;absorb(b?.included??[]);result.pagesCollected+=1;}catch{break;}
}
const ids=[...byId.keys()].slice(0,CONFIG.hiringTeamLimit);
const EXTRACT=VIEW_EXTRACT;
for(const id of ids){
  try{
    await p.goto("https://www.linkedin.com/jobs/view/"+id+"/",{waitUntil:"domcontentloaded",timeout:45000});
    await p.waitForTimeout(2500);
    const view=await p.evaluate(EXTRACT);
    const team=(view.team??[]).map(t=>({name:t.name,profileUrl:t.profileUrl,degree:t.degree||"",headline:t.headline||""}));
    result.jobs.push({id,title:(byId.get(id)?.title||view.title||"").trim(),company:view.company||"",location:view.location||"",postingUrl:"https://www.linkedin.com/jobs/view/"+id+"/",hiringTeam:team,hasHiringTeam:team.length>0});
  }catch{
    result.jobs.push({id,title:(byId.get(id)?.title||"").trim(),company:"",location:"",postingUrl:"https://www.linkedin.com/jobs/view/"+id+"/",hiringTeam:[],hasHiringTeam:false});
  }
}
try{p.removeListener("response",onResponse);}catch{}
console.log(JSON.stringify({ok:true,data:result}));
`;

/**
 * Build the search + enrichment script. Collects the `voyagerJobsDashJobCards`
 * XHR for the search, paginates by replaying with `start` bumped, then loads
 * each job's direct view to extract company, location, and hiring team.
 */
export function buildSearchScript(config: {
  readonly searchUrl: string;
  readonly pages: number;
  readonly hiringTeamLimit: number;
}): { readonly script: string; readonly timeoutMs: number } {
  if (!Number.isSafeInteger(config.pages) || config.pages < 1 || config.pages > 10)
    throw new TypeError("pages must be 1..10");
  if (
    !Number.isSafeInteger(config.hiringTeamLimit) ||
    config.hiringTeamLimit < 1 ||
    config.hiringTeamLimit > 50
  )
    throw new TypeError("hiringTeamLimit must be 1..50");
  const jobCountTarget = config.pages * 25;
  const cfg = {
    searchUrl: config.searchUrl,
    pages: config.pages,
    hiringTeamLimit: config.hiringTeamLimit,
    jobCountTarget,
  };
  // Top-level awaits only: the playwriter executor does not await an async IIFE.
  const source = `const CONFIG=${JSON.stringify(cfg)};const VIEW_EXTRACT=${EXTRACT_VIEW};\n${PAGE_PICKUP}\n${SEARCH_RESULT}`;
  return { script: source, timeoutMs: SEARCH_TIMEOUT_MS };
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
