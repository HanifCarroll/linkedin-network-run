import { buildAnalyticsPlaywriterScript } from "../analytics/playwriter-scripts/templates.ts";
import type { AnalyticsCommandConfig } from "../analytics/types.ts";
import { buildPostSendPageValidationJs } from "../core/linkedin-url.ts";
import { parseSendPreparationReceiptId, SEND_PREPARATION_TARGET } from "./send.ts";
import { assertNetworkSourceContract, resolveNetworkSourceContract } from "./source-capture.ts";
import type {
  AnalyticsPlaywriterCommand,
  BrowserActionKind,
  CandidateIdentity,
  CompiledScriptDescriptor,
  NetworkCommand,
  NetworkSourceContract,
  PlaywriterCommand,
  ProgressState,
  SendPreparationReceipt,
} from "./types.ts";
import {
  SEND_PREPARATION_STATE_KEY,
  SEND_PREPARATION_TTL_MS,
  SOURCE_CAPTURE_STATE_KEY,
} from "./types.ts";
import { assertAllowedWorkflowUrl, WORKFLOW_STATE_KEYS } from "./urls.ts";
import { assertCandidateIdentity, assertSendPreparationReceipt } from "./validation.ts";
export interface NetworkScriptInput {
  readonly url?: string;
  readonly candidate?: CandidateIdentity;
  readonly attemptId?: string;
  readonly sendPreparation?: SendPreparationReceipt;
  readonly sourceContract?: NetworkSourceContract;
  readonly budget?: number;
  readonly pacingMs?: number;
}

export type AdapterPlanStep =
  | { readonly op: "exact_page"; readonly stateKey: string; readonly url: string }
  | { readonly op: "observe"; readonly label: string }
  | { readonly op: "logs" }
  | { readonly op: "navigate"; readonly stateKey: string; readonly url: string }
  | { readonly op: "click_role"; readonly role: "button" | "link"; readonly name: string };
export interface GenericAdapterDefinition<TCommand extends string = string> {
  readonly id: string;
  readonly command: TCommand;
  readonly action: BrowserActionKind;
  readonly phases: readonly ProgressState[];
  readonly plan: readonly AdapterPlanStep[];
}

const controlled = new WeakSet<object>();
const ATTEMPT_ID = /^[A-Za-z0-9:_-]{8,200}$/;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const literal = (v: unknown) => JSON.stringify(v).replaceAll("<", "\\u003c");
const progress = (s: ProgressState, detail?: string) =>
  `await __progress(${literal(s)}${detail ? `,${detail}` : ""});`;
const logs = `const __logs=await getLatestLogs({page:p,sinceLastCall:true});const __diagnosticSummary=__selectDiagnostics(__logs);${progress("logs_captured")}`;
const result = (command: string, body: string) =>
  `return __emitControl({schemaVersion:1,command:${literal(command)},ok:true,${body},logs:__diagnosticSummary});`;
const prelude = [
  `const __fs=require("node:fs");const __crypto=require("node:crypto");const __Buffer=require("node:buffer").Buffer;`,
  `const __append=(value)=>__fs.appendFileSync(__PROGRESS_PATH__,JSON.stringify(value)+"\\n");`,
  `const __hash=(value)=>__crypto.createHash("sha256").update(value).digest("hex");`,
  `const __progress=async(state,detail)=>{const e={invocationId:__INVOCATION_ID__,nonce:__HANDOFF_NONCE__,command:__COMMAND__,state,timestamp:new Date().toISOString(),...(__CANDIDATE__?{candidate:__CANDIDATE__}:{}),...(detail?{detail}:{})};__append(e);};`,
  `const __selectDiagnostics=(logs)=>{`,
  `if(!Array.isArray(logs))throw new Error("DIAGNOSTIC_LOGS_INVALID");`,
  `const terminal=/\\b429\\b|too many requests|weekly (?:invitation|connection) limit|security verification|quick security check|captcha|linkedin\\.com\\/checkpoint|checkpoint|unusual activity|temporarily restricted|sign in|join linkedin|session expired|login required|net::ERR_(?:CONNECTION_REFUSED|INTERNET_DISCONNECTED|NAME_NOT_RESOLVED|NETWORK_CHANGED)/i;`,
  `const generic=/net::ERR_FAILED/i;const sampleLimit=32;const relevant=new Map(),other=new Map(),genericFailed=new Map();let relevantCount=0,otherCount=0,genericFailedCount=0;`,
  `const add=(target,text,includeText)=>{const bytes=__Buffer.byteLength(text);const sha256=__hash(text);const prior=target.get(sha256);if(prior&&includeText&&prior.text!==text)throw new Error("DIAGNOSTIC_HASH_COLLISION");if(prior){prior.count+=1;return;}if(target.size<sampleLimit||includeText)target.set(sha256,{sha256,count:1,bytes,...(includeText?{text}:{})});};`,
  `for(const value of logs){let text;try{text=typeof value==="string"?value:JSON.stringify(value);}catch{throw new Error("DIAGNOSTIC_LOG_INVALID");}if(typeof text!=="string")throw new Error("DIAGNOSTIC_LOG_INVALID");if(terminal.test(text)){relevantCount+=1;add(relevant,text,true);}else if(generic.test(text)){genericFailedCount+=1;add(genericFailed,text,false);}else{otherCount+=1;add(other,text,false);}}`,
  `const diagnostic={schemaVersion:1,kind:"playwriter_diagnostic_selection",selectionStep:"terminal-preserving-diagnostic-selection-v2",invocationId:__INVOCATION_ID__,nonce:__HANDOFF_NONCE__,sourceCount:logs.length,relevantCount,otherCount,genericNetErrFailedCount:genericFailedCount,sampleLimit,relevant:[...relevant.values()],otherSample:[...other.values()],genericNetErrFailedSample:[...genericFailed.values()]};`,
  `const json=JSON.stringify(diagnostic);if(__Buffer.byteLength(json)>131072)throw new Error("DIAGNOSTIC_ARTIFACT_TOO_LARGE");__append(diagnostic);return Object.freeze({schemaVersion:1,kind:"playwriter_diagnostic_summary",selectionStep:diagnostic.selectionStep,sourceCount:diagnostic.sourceCount,relevantCount,otherCount,genericNetErrFailedCount:genericFailedCount,sha256:__hash(json),artifact:"diagnostics.json"});};`,
  `const __emitControl=(result)=>{const json=JSON.stringify(result);const bytes=__Buffer.byteLength(json);if(bytes>262144)throw new Error("CONTROL_ARTIFACT_TOO_LARGE");const record={schemaVersion:1,kind:"playwriter_control_record",invocationId:__INVOCATION_ID__,nonce:__HANDOFF_NONCE__,result};__append(record);return "__LINKEDIN_TOOLS_CONTROL_V1__"+JSON.stringify({schemaVersion:1,kind:"playwriter_control_pointer",invocationId:__INVOCATION_ID__,nonce:__HANDOFF_NONCE__,bytes,sha256:__hash(json)});};`,
].join("");

function issue<T extends PlaywriterCommand>(value: {
  readonly command: T;
  readonly action: BrowserActionKind;
  readonly phases: readonly ProgressState[];
  readonly source: string;
  readonly definitionId: string;
  readonly candidate?: CandidateIdentity;
  readonly sendPreparation?: SendPreparationReceipt;
  readonly sourceContract?: NetworkSourceContract;
}) {
  const descriptor = Object.freeze(value) as CompiledScriptDescriptor<T>;
  controlled.add(descriptor);
  return descriptor;
}

function countActions(plan: readonly AdapterPlanStep[]): number {
  return plan.filter((s) => s.op === "navigate" || s.op === "click_role").length;
}

/** Controlled generic surface: callers provide a closed structured plan, never executable source. */
export function compileGenericAdapter<T extends string>(
  definition: GenericAdapterDefinition<T>,
): CompiledScriptDescriptor<T> {
  if (!/^[a-z][a-z0-9-]{1,63}$/.test(definition.command)) throw new TypeError("invalid command");
  if (!/^[a-z][a-z0-9_.-]{3,80}$/.test(definition.id)) throw new TypeError("invalid definition id");
  if (
    definition.action === "custom" ||
    definition.action === "send" ||
    definition.action === "connect"
  )
    throw new TypeError("generic adapters cannot issue sensitive actions");
  const actions = countActions(definition.plan);
  if (actions !== (definition.action === "none" ? 0 : 1))
    throw new TypeError("plan/action mismatch");
  if (!definition.plan.some((s) => s.op === "logs"))
    throw new TypeError("adapter must capture logs");
  let source = `${prelude}let p=page;`;
  for (const step of definition.plan) {
    if (step.op === "exact_page")
      source += `p=state[${literal(step.stateKey)}];if(!p||p.url()!==${literal(step.url)})throw new Error("WRONG_PAGE");`;
    else if (step.op === "observe") source += `void ${literal(step.label)};`;
    else if (step.op === "logs") source += logs;
    else if (step.op === "navigate")
      source += `p=state[${literal(step.stateKey)}]??page;state[${literal(step.stateKey)}]=p;await p.goto(${literal(step.url)});`;
    else
      source += `const target=p.getByRole(${literal(step.role)},{name:${literal(step.name)},exact:true});if(await target.count()!==1)throw new Error("SELECTOR_CONTRACT");await target.click();`;
  }
  source += result(definition.command, "data:{adapter:true}");
  return issue({
    command: definition.command,
    action: definition.action,
    phases: Object.freeze([...definition.phases]),
    source,
    definitionId: definition.id,
  });
}

export function isControlledCompiledScript(v: unknown): v is CompiledScriptDescriptor {
  return typeof v === "object" && v !== null && controlled.has(v);
}

const usablePage = (key: string) =>
  `let p=null;{const stored=state[${literal(key)}];if(stored&&!stored.isClosed())p=stored;else{const candidates=context.pages();const open=candidates.find((candidate)=>!candidate.isClosed());p=open??(await context.newPage());}state[${literal(key)}]=p;}`;
const exactPage = (key: string, url: string) => {
  const expected = new URL(url);
  if (expected.pathname === "/sales/search/people" && expected.searchParams.has("savedSearchId"))
    return `${usablePage(key)}const expectedPageUrl=new URL(${literal(url)});const observedPageUrl=new URL(p.url());const observedPageKeys=[...observedPageUrl.searchParams.keys()];const exactSourcePage=observedPageUrl.origin===expectedPageUrl.origin&&observedPageUrl.pathname===expectedPageUrl.pathname&&observedPageUrl.hash===""&&observedPageUrl.searchParams.get("savedSearchId")===expectedPageUrl.searchParams.get("savedSearchId")&&(observedPageKeys.length===1||(observedPageKeys.length===2&&observedPageKeys.includes("sessionId")&&(observedPageUrl.searchParams.get("sessionId")?.trim().length??0)>0));if(!exactSourcePage)throw new Error("WRONG_PAGE");`;
  return `${usablePage(key)}if(p.url()!==${literal(url)})throw new Error("WRONG_PAGE");`;
};
const scrollResultsStep = `let scrollState=null;for(let scrollProbe=0;scrollProbe<3&&scrollState===null;scrollProbe+=1){try{const candidate=await p.evaluate((selector)=>{const element=document.querySelector(selector);if(!element)return {before:0,after:0,max:0};const before=element.scrollTop;const max=Math.max(0,element.scrollHeight-element.clientHeight);const step=Math.max(400,Math.floor(element.clientHeight*0.8));element.scrollTop=Math.min(max,before+step);return {before,after:element.scrollTop,max};},"#search-results-container");if(candidate&&Number.isSafeInteger(candidate.before)&&Number.isSafeInteger(candidate.after)&&Number.isSafeInteger(candidate.max)&&candidate.before>=0&&candidate.after>=candidate.before&&candidate.max>=candidate.after)scrollState=candidate;}catch{}if(scrollState===null)await p.waitForTimeout(1000);}if(scrollState===null)scrollState={before:0,after:0,max:0};`;
const candidateRow = (c: CandidateIdentity) => {
  return `let row=null;let lead=null;for(let pageAttempt=0;pageAttempt<10&&row===null;pageAttempt+=1){const resultRows=p.locator("li.artdeco-list__item:has(a[href*='/sales/lead/'])");for(let waitAttempt=0;waitAttempt<45&&(await resultRows.count())===0;waitAttempt+=1)await p.waitForTimeout(1000);for(let scrollAttempt=0;scrollAttempt<50&&row===null;scrollAttempt+=1){for(const r of await resultRows.all()){await r.scrollIntoViewIfNeeded();const marker=r.locator("[data-scroll-into-view]");if(await marker.count()!==1)throw new Error("SELECTOR_CONTRACT");const rawIdentity=await marker.getAttribute("data-scroll-into-view");const identityMatch=typeof rawIdentity==="string"?/^urn:li:fs_salesProfile:(?:\\(([A-Za-z0-9_-]+),[^)]*\\)|([A-Za-z0-9_-]+))$/.exec(rawIdentity):null;const rowSalesLeadId=identityMatch?.[1]??identityMatch?.[2]??null;if(typeof rowSalesLeadId!=="string")throw new Error("SOURCE_MISMATCH");const rowLeads=r.locator("a[href*='/sales/lead/']");if(await rowLeads.count()<1)throw new Error("SELECTOR_CONTRACT");const rowLead=rowLeads.first();const href=await rowLead.getAttribute("href");if(typeof href!=="string")throw new Error("SOURCE_MISMATCH");const observedLeadUrl=new URL(href,"https://www.linkedin.com");const leadMatch=/^\\/sales\\/lead\\/([^/,]+)(?:,.*)?\\/?$/.exec(observedLeadUrl.pathname);const salesLeadId=leadMatch?.[1]??null;if(observedLeadUrl.origin!=="https://www.linkedin.com"||salesLeadId!==rowSalesLeadId)throw new Error("SOURCE_MISMATCH");if(rowSalesLeadId===${literal(c.salesLeadId)}){if(row)throw new Error("SELECTOR_CONTRACT");row=r;lead=rowLead;break;}}if(row)break;${scrollResultsStep}if(scrollState.after===scrollState.before)break;await p.waitForTimeout(700);}if(row)break;const namedPagination=p.getByRole("navigation",{name:"Pagination",exact:true});const namedPaginationCount=await namedPagination.count();const allPagination=p.getByRole("navigation");const allPaginationCount=await allPagination.count();if(namedPaginationCount>1||(namedPaginationCount===0&&allPaginationCount>1))throw new Error("SELECTOR_CONTRACT");const hasPaginationContainer=namedPaginationCount===1||allPaginationCount===1;const paginationLocator=hasPaginationContainer?(namedPaginationCount===1?namedPagination:allPagination.first()):null;const next=paginationLocator?paginationLocator.getByRole("button",{name:"Next",exact:true}).first():p.getByRole("button",{name:"Next",exact:true}).first();if(await next.count()!==1||!(await next.isVisible())||await next.isDisabled())break;const previousUrl=p.url();const previousCurrent=paginationLocator?paginationLocator.locator("[aria-current='page']").first():p.locator("[aria-current='page']").first();const previousCursor=await previousCurrent.count()===1?await previousCurrent.getAttribute("aria-label"):null;await next.click();let advanced=false;for(let waitAttempt=0;waitAttempt<45;waitAttempt+=1){await p.waitForTimeout(1000);const nextUrl=p.url();const nextCurrent=paginationLocator?paginationLocator.locator("[aria-current='page']").first():p.locator("[aria-current='page']").first();const nextCursor=await nextCurrent.count()===1?await nextCurrent.getAttribute("aria-label"):null;if(nextUrl!==previousUrl||nextCursor!==previousCursor){advanced=true;break;}}if(!advanced)throw new Error("PAGINATION_STALLED");}if(!row||!lead)throw new Error("CANDIDATE_ABSENT");`;
};
const LINKEDIN_DIALOG_SELECTOR = "[role='dialog'], .artdeco-modal, [data-test-modal]";
const SEND_INVITATION_LABEL =
  "/^(Send Invitation|Send invite|Send now|Send without a note|Send)$/i";
const visibleSendControl = () =>
  `let send=null;let sendDialog=null;let observedButtons=[];const sendDeadline=Date.now()+6000;for(let sendAttempt=0;sendAttempt<25&&Date.now()<=sendDeadline;sendAttempt+=1){send=null;sendDialog=null;observedButtons=[];for(const candidateModal of await p.locator(${literal(LINKEDIN_DIALOG_SELECTOR)}).all()){if(!(await candidateModal.isVisible()))continue;let modalSend=null;for(const button of await candidateModal.locator("button").all()){if(!(await button.isVisible()))continue;const text=String(await button.textContent()??"").replace(/\\s+/g," ").trim();const aria=String(await button.getAttribute("aria-label")??"").replace(/\\s+/g," ").trim();const label=text||aria;if(label)observedButtons.push(label);if(${SEND_INVITATION_LABEL}.test(label)){if(modalSend!==null)throw new Error("SELECTOR_CONTRACT");modalSend=button;}}if(modalSend===null)continue;if(send!==null)throw new Error("SELECTOR_CONTRACT");send=modalSend;sendDialog=candidateModal;}if(send!==null&&sendDialog!==null)break;await p.waitForTimeout(250);}if(send===null||sendDialog===null)throw new Error("MISSING_SEND labels="+JSON.stringify(observedButtons).slice(0,2000));if(await sendDialog.locator("input[type='email'], input[name*='email' i]").count()>0)throw new Error("EMAIL_REQUIRED");if(await send.isDisabled())throw new Error("DISABLED_SEND");`;

const visibleDialogCount = () =>
  `const dialogs=await p.locator(${literal(LINKEDIN_DIALOG_SELECTOR)}).all();let visibleDialogCount=0;for(const candidateModal of dialogs)if(await candidateModal.isVisible())visibleDialogCount+=1;if(visibleDialogCount!==1)throw new Error("SELECTOR_CONTRACT");`;
const postSendPageValidation = (candidateExpression: string) =>
  buildPostSendPageValidationJs(candidateExpression);
const postSendPendingEvidence = () =>
  `let pendingCount=0;if(isCandidateResultsPage){const postRows=p.locator("li.artdeco-list__item:has(a[href*='/sales/lead/'])");for(let waitAttempt=0;waitAttempt<10&&(await postRows.count())===0;waitAttempt+=1)await p.waitForTimeout(500);for(const postRow of await postRows.all()){const marker=postRow.locator("[data-scroll-into-view]");if(await marker.count()!==1)continue;const rawIdentity=await marker.getAttribute("data-scroll-into-view");const identityMatch=typeof rawIdentity==="string"?/^urn:li:fs_salesProfile:(?:\\(([A-Za-z0-9_-]+),[^)]*\\)|([A-Za-z0-9_-]+))$/.exec(rawIdentity):null;const rowSalesLeadId=identityMatch?.[1]??identityMatch?.[2]??null;if(rowSalesLeadId!==postCandidate.salesLeadId)continue;const visiblePending=postRow.getByText("Pending",{exact:true});if(await visiblePending.count()>0)pendingCount=1;const postTrigger=postRow.locator("button[aria-label^='See more actions for']").first();if(await postTrigger.count()!==1)break;const postActionClick=async(target)=>{try{await target.click({timeout:8000});}catch{try{await target.click({timeout:3000,force:true});}catch{await target.evaluate((element)=>element.click());}}};const postMenuId=await postTrigger.getAttribute("aria-controls");await postActionClick(postTrigger);await p.waitForTimeout(500);let postMenu=postMenuId?p.locator("#"+postMenuId).first():p.locator("[data-popper-placement]").last();if(await postMenu.count()===0)postMenu=p.locator("[data-popper-placement]").last();if(await postMenu.count()>0){for(const item of await postMenu.locator("button,a,[role=menuitem]").all()){const text=String(await item.textContent()??"").replace(/\\s+/g," ").trim();const aria=String(await item.getAttribute("aria-label")??"").replace(/\\s+/g," ").trim();if(/^(Connect\\s*[-–—]\\s*)?Pending$/i.test(text)||/^(Connect\\s*[-–—]\\s*)?Pending$/i.test(aria))pendingCount=1;}}await p.keyboard.press("Escape");break;}}`;
const postSendEvidence = `let modalCount=0;let sendControlCount=0;for(const candidateModal of await p.locator(${literal(LINKEDIN_DIALOG_SELECTOR)}).all()){if(!(await candidateModal.isVisible()))continue;modalCount+=1;for(const button of await candidateModal.locator("button").all()){if(!(await button.isVisible()))continue;const text=String(await button.textContent()??"").replace(/\\s+/g," ").trim();const aria=String(await button.getAttribute("aria-label")??"").replace(/\\s+/g," ").trim();if(${SEND_INVITATION_LABEL}.test(text||aria))sendControlCount+=1;}}`;
const connectCandidate = (c: CandidateIdentity) => {
  const triggerSelector = 'button[aria-label^="See more actions for"]';
  return `${before}${exactPage(WORKFLOW_STATE_KEYS.candidateResults, c.searchUrl)}${candidateRow(c)}const trigger=row.locator(${literal(triggerSelector)}).first();if(await trigger.count()!==1)throw new Error("MISSING_MORE_ACTIONS");const clickAction=async(target)=>{try{await target.click({timeout:8000});}catch{try{await target.click({timeout:3000,force:true});}catch{await target.evaluate((element)=>element.click());}}};const menuId=await trigger.getAttribute("aria-controls");await clickAction(trigger);await p.waitForTimeout(500);let menu=menuId?p.locator("#"+menuId).first():p.locator("[data-popper-placement]").last();let menuCount=await menu.count();if(menuCount===0){menu=p.locator("[data-popper-placement]").last();menuCount=await menu.count();}if(menuCount===0)throw new Error("MISSING_CONNECT_MENU menu_id="+String(menuId??"")+" menu_count=0 popper_count="+String(await p.locator("[data-popper-placement]").count()));const menuItems=menu.locator("button,a,[role=menuitem]");const menuItemList=await menuItems.all();let connect=null;let pending=false;const labels=[];for(const item of menuItemList){const text=String(await item.textContent()??"").replace(/\\s+/g," ").trim();const aria=String(await item.getAttribute("aria-label")??"").replace(/\\s+/g," ").trim();if(text||aria)labels.push({text,aria});if(/^(Connect\\s*[-–—]\\s*)?Pending$/i.test(text)||/^(Connect\\s*[-–—]\\s*)?Pending$/i.test(aria))pending=true;if(/^Connect$/i.test(text)||/^Connect$/i.test(aria)){if(connect)throw new Error("SELECTOR_CONTRACT");connect=item;}}if(pending)throw new Error("ALREADY_PENDING");if(!connect)throw new Error("MISSING_CONNECT_MENU menu_id="+String(menuId??"")+" item_count="+String(menuItemList.length)+" labels="+JSON.stringify(labels).slice(0,2000));${progress("connect_started")}await clickAction(connect);${progress("connect_returned")}${after}${finish("click-connect-menu-item", `data:{candidate:${literal(c)},predecessorAction:"candidate-menu-sequence",actionId:__INVOCATION_ID__+":connect"}`)}`;
};
const before = progress("observation_before");
const after = progress("observation_after");
const finish = (command: string, body: string) => `${logs}${result(command, body)}`;
const sourceRowEligibility = () =>
  `const actionTrigger=r.locator("button[aria-label^='See more actions for']").first();if(await actionTrigger.count()!==1)throw new Error("MISSING_MORE_ACTIONS");const actionClick=async(target)=>{try{await target.click({timeout:8000});}catch{try{await target.click({timeout:3000,force:true});}catch{await target.evaluate((element)=>element.click());}}};const actionMenuId=await actionTrigger.getAttribute("aria-controls");await actionClick(actionTrigger);await p.waitForTimeout(500);let actionMenu=actionMenuId?p.locator("#"+actionMenuId).first():p.locator("[data-popper-placement]").last();if(await actionMenu.count()===0)actionMenu=p.locator("[data-popper-placement]").last();if(await actionMenu.count()===0)throw new Error("MISSING_CONNECT_MENU");const actionItems=actionMenu.locator("button,a,[role=menuitem]");let actionConnectable=false;let actionPending=false;for(const item of await actionItems.all()){const text=String(await item.textContent()??"").replace(/\\s+/g," ").trim();const aria=String(await item.getAttribute("aria-label")??"").replace(/\\s+/g," ").trim();const label=text||aria;if(/^Connect$/i.test(label))actionConnectable=true;if(/^(Connect\\s*[-–—]\\s*)?Pending$/i.test(label))actionPending=true;}await p.keyboard.press("Escape");if(actionPending||!actionConnectable)continue;`;

const exhaustResultsScroll = `let scrollPasses=0;let stagnantPasses=0;for(;scrollPasses<50;scrollPasses+=1){${scrollResultsStep}if(scrollState.after===scrollState.before){stagnantPasses+=1;if(stagnantPasses>=3)break;}else stagnantPasses=0;await p.waitForTimeout(700);}`;

function walkListBody(
  url: string,
  sourceContract: NetworkSourceContract,
  budget: number,
  pacingMs: number,
): string {
  return [
    before,
    progress("navigation_started"),
    `const sourceContract=${literal(sourceContract)};`,
    `const budget=${literal(budget)};`,
    `const pacingMs=${literal(pacingMs)};`,
    `const maxPages=10;`,
    usablePage(WORKFLOW_STATE_KEYS.candidateResults),
    `const xhrResponses=[];const xhrListener=async(res)=>{try{const u=res.url();if(!u.includes("salesApiLeadSearch"))return;const startParam=new URL(u).searchParams.get("start");let body=null;try{body=await res.json();}catch{}if(body&&body.paging&&Array.isArray(body.elements)){xhrResponses.push({start:Number(startParam)||0,elements:body.elements,total:Number(body.paging.total)||null});}}catch{}};p.on("response",xhrListener);`,
    `await p.goto(${literal(url)},{waitUntil:"domcontentloaded"});`,
    progress("navigation_returned"),
    `if(p.url()!==sourceContract.searchUrl)throw new Error("WRONG_PAGE");`,
    `const leadIdFrom=(urn)=>{const m=/^urn:li:fs_salesProfile:\\(([A-Za-z0-9_-]+),/.exec(String(urn||""));return m?m[1]:null;};`,
    `const sent=[];const skipped=[];let pagesWalked=0;let complete=false;`,
    `const clickAction=async(target)=>{try{await target.click({timeout:8000});}catch{try{await target.click({timeout:3000,force:true});}catch{await target.evaluate((element)=>element.click());}}};`,
    `for(let pageIndex=0;pageIndex<maxPages&&sent.length<budget;pageIndex+=1){`,
    `pagesWalked+=1;`,
    `let pageElements=null;for(let waitAttempt=0;waitAttempt<60&&pageElements===null;waitAttempt+=1){const last=xhrResponses[xhrResponses.length-1];if(last&&last.elements.length>0)pageElements=last.elements;else await p.waitForTimeout(500);}`,
    `const byId=new Map();if(pageElements!==null){for(const el of pageElements){const id=leadIdFrom(el.entityUrn);if(id)byId.set(id,{name:String(el.fullName||"").trim()||"unknown",pending:!!el.pendingInvitation,degree:Number(el.degree)||0});}}`,
    exhaustResultsScroll,
    `const resultRows=p.locator("li.artdeco-list__item:has(a[href*='/sales/lead/'])");`,
    `for(let waitAttempt=0;waitAttempt<45&&(await resultRows.count())===0;waitAttempt+=1)await p.waitForTimeout(1000);`,
    `const rows=await resultRows.all();`,
    `let walked=0;let malformed=0;let connectableOnPage=0;`,
    `for(const r of rows){`,
    `if(sent.length>=budget)break;`,
    `await r.scrollIntoViewIfNeeded();`,
    `const marker=r.locator("[data-scroll-into-view]");`,
    `if(await marker.count()!==1){malformed+=1;walked+=1;const unreachableName="unknown";skipped.push({rowIdentity:"",name:unreachableName,reason:"unreachable"});continue;}`,
    `const rawIdentity=await marker.getAttribute("data-scroll-into-view");`,
    `const identityMatch=typeof rawIdentity==="string"?/^urn:li:fs_salesProfile:(?:\\(([A-Za-z0-9_-]+),[^)]*\\)|([A-Za-z0-9_-]+))$/.exec(rawIdentity):null;`,
    `const salesNavId=identityMatch?.[1]??identityMatch?.[2]??null;`,
    `if(typeof salesNavId!=="string"||typeof rawIdentity!=="string"){malformed+=1;walked+=1;skipped.push({rowIdentity:typeof rawIdentity==="string"?rawIdentity:"",name:"unknown",reason:"unreachable"});continue;}`,
    `const rowIdentity="urn:li:fs_salesProfile:"+salesNavId;`,
    `walked+=1;`,
    `const meta=byId.get(salesNavId)??null;`,
    `if(meta&&meta.pending){skipped.push({rowIdentity,name:meta.name,reason:"already_pending"});continue;}`,
    `if(meta&&meta.degree===1){skipped.push({rowIdentity,name:meta.name,reason:"first_degree"});continue;}`,
    `const trigger=r.locator("button[aria-label^='See more actions for']").first();`,
    `if(await trigger.count()!==1){skipped.push({rowIdentity,name:meta?meta.name:"unknown",reason:"unreachable"});continue;}`,
    `const ariaLabel=String(await trigger.getAttribute("aria-label")??"").replace(/\\s+/g," ").trim();`,
    `const nameMatch=/^See more actions for\\s+(.+)$/i.exec(ariaLabel);`,
    `const name=(meta&&meta.name!=="unknown")?meta.name:(nameMatch?.[1]?.trim()||"unknown");`,
    `if(!name||name==="unknown"){skipped.push({rowIdentity,name,reason:"unreachable"});continue;}`,
    `const menuId=await trigger.getAttribute("aria-controls");`,
    `await clickAction(trigger);`,
    `await p.waitForTimeout(500);`,
    `let menu=menuId?p.locator("#"+menuId).first():p.locator("[data-popper-placement]").last();`,
    `if(await menu.count()===0)menu=p.locator("[data-popper-placement]").last();`,
    `if(await menu.count()===0){skipped.push({rowIdentity,name,reason:"unreachable"});await p.keyboard.press("Escape");continue;}`,
    `let connectItem=null;let pending=false;let emailRequired=false;`,
    `for(const item of await menu.locator("button,a,[role=menuitem]").all()){`,
    `const text=String(await item.textContent()??"").replace(/\\s+/g," ").trim();`,
    `const aria=String(await item.getAttribute("aria-label")??"").replace(/\\s+/g," ").trim();`,
    `const label=text||aria;`,
    `if(/^Connect$/i.test(label))connectItem=item;`,
    `if(/^(Connect\\s*[-–—]\\s*)?Pending$/i.test(label))pending=true;`,
    `if(/email/i.test(label)&&/required|connect/i.test(label))emailRequired=true;`,
    `}`,
    `if(pending){await p.keyboard.press("Escape");skipped.push({rowIdentity,name,reason:"already_pending"});continue;}`,
    `if(emailRequired||connectItem===null){await p.keyboard.press("Escape");skipped.push({rowIdentity,name,reason:emailRequired?"email_required":"unreachable"});continue;}`,
    `connectableOnPage+=1;`,
    `await clickAction(connectItem);`,
    `await p.waitForTimeout(500);`,
    `try{`,
    visibleSendControl(),
    `await send.click();`,
    `sent.push({rowIdentity,name});`,
    `}catch(e){const emailRequired=String((e&&e.message)||e).includes("EMAIL_REQUIRED");if(!emailRequired)throw e;await p.keyboard.press("Escape");skipped.push({rowIdentity,name,reason:"email_required"});continue;}`,
    `if(sent.length<budget)await p.waitForTimeout(pacingMs);`,
    `}`,
    `if(walked>0&&malformed/walked>0.5)throw new Error("SOURCE_MISMATCH");`,
    `if(sent.length>=budget){complete=true;break;}`,
    `let next=null;const nextRole=p.getByRole("button",{name:/^Next/i});if(await nextRole.count()===1)next=nextRole.first();else{const nextAria=p.locator("button[aria-label*='Next' i]");if(await nextAria.count()===1)next=nextAria.first();}`,
    `if(next===null||!(await next.isVisible())||await next.isDisabled()){complete=true;break;}`,
    `const previousUrl=p.url();`,
    `const lastStart=xhrResponses.length>0?xhrResponses[xhrResponses.length-1].start:-1;`,
    `const lastXhrCount=xhrResponses.length;`,
    `await next.click();`,
    `let advanced=false;`,
    `for(let waitAttempt=0;waitAttempt<45;waitAttempt+=1){await p.waitForTimeout(1000);const nextUrl=p.url();const latest=xhrResponses[xhrResponses.length-1];if(nextUrl!==previousUrl||(xhrResponses.length>lastXhrCount&&latest&&latest.start!==lastStart)){advanced=true;break;}}`,
    `if(!advanced)complete=true;`,
    `}`,
    `if(sent.length>=budget||pagesWalked>=maxPages)complete=true;`,
    `if(!complete&&sent.length<budget)complete=true;`,
    after,
    finish(
      "walk-list",
      "data:{sourceId:sourceContract.sourceId,sent,skipped,pagesWalked,complete}",
    ),
  ].join("");
}

function networkIssue(
  command: NetworkCommand,
  action: BrowserActionKind,
  phases: readonly ProgressState[],
  source: string,
  candidate?: CandidateIdentity,
  sendPreparation?: SendPreparationReceipt,
  sourceContract?: NetworkSourceContract,
) {
  const isolatedSource = source.replaceAll("__crypto", "__actionCrypto");
  return issue({
    command,
    action,
    phases: Object.freeze([...phases]),
    source: prelude + isolatedSource,
    definitionId: `network.${command}.${sourceContract ? "v2" : "v1"}`,
    ...(candidate ? { candidate } : {}),
    ...(sendPreparation ? { sendPreparation } : {}),
    ...(sourceContract ? { sourceContract } : {}),
  });
}

function sourceNavigationBody(url: string, sourceContract: NetworkSourceContract): string {
  return `${before}${progress("navigation_started")}const sourceContract=${literal(sourceContract)};${usablePage(WORKFLOW_STATE_KEYS.candidateResults)}await p.goto(${literal(url)},{waitUntil:"domcontentloaded"});${progress("navigation_returned")}if(p.url()!==sourceContract.searchUrl)throw new Error("WRONG_PAGE");const priorState=state[${literal(SOURCE_CAPTURE_STATE_KEY)}];if(priorState!==undefined&&(!priorState||priorState.schemaVersion!==1||priorState.kind!=="network_source_capture_state"||typeof priorState.bySource!=="object"||priorState.bySource===null))throw new Error("SOURCE_MISMATCH");const priorBySource=priorState?.bySource??{};const prior=priorBySource[sourceContract.sourceId];if(prior!==undefined&&(prior.sourceId!==sourceContract.sourceId||prior.searchUrl!==sourceContract.searchUrl||prior.sourceContractFingerprint!==sourceContract.contractFingerprint||!Number.isSafeInteger(prior.reloadGeneration)||prior.reloadGeneration<1))throw new Error("SOURCE_MISMATCH");const reloadGeneration=(prior?.reloadGeneration??0)+1;const navigatedAt=new Date().toISOString();const staged=Object.freeze({schemaVersion:1,kind:"network_source_reload",sourceId:sourceContract.sourceId,searchUrl:sourceContract.searchUrl,sourceContractFingerprint:sourceContract.contractFingerprint,navigationInvocationId:__INVOCATION_ID__,reloadIdentity:__INVOCATION_ID__+":reload",reloadGeneration,navigatedAt});state[${literal(SOURCE_CAPTURE_STATE_KEY)}]=Object.freeze({schemaVersion:1,kind:"network_source_capture_state",bySource:Object.freeze({...priorBySource,[sourceContract.sourceId]:staged})});${after}${finish("navigate-candidate-results", "data:{url:p.url(),sourceContract,reload:{navigationInvocationId:staged.navigationInvocationId,reloadIdentity:staged.reloadIdentity,reloadGeneration:staged.reloadGeneration,navigatedAt:staged.navigatedAt}}")}`;
}

function sourceCaptureBody(url: string, sourceContract: NetworkSourceContract): string {
  const sourcePage = [
    `const visibleCount=async locator=>{let count=0;for(const item of await locator.all())if(await item.isVisible())count+=1;return count;};`,
    `for(let pageAttempt=0;pageAttempt<10;pageAttempt+=1){`,
    `pagesVisited=pageAttempt+1;const observedPageUrl=new URL(p.url());const expectedPageUrl=new URL(sourceContract.searchUrl);if(observedPageUrl.origin!==expectedPageUrl.origin||observedPageUrl.pathname!==expectedPageUrl.pathname||observedPageUrl.hash!==""||observedPageUrl.searchParams.get("savedSearchId")!==sourceContract.savedSearchId)throw new Error("WRONG_PAGE");`,
    `const resultRows=p.locator("li.artdeco-list__item:has(a[href*='/sales/lead/'])");for(let waitAttempt=0;waitAttempt<45&&(await resultRows.count())===0;waitAttempt+=1)await p.waitForTimeout(1000);`,
    `const resultsContainer=p.locator("#search-results-container");const resultsContainerCount=await resultsContainer.count();if(resultsContainerCount>1)throw new Error("SELECTOR_CONTRACT");const resultsContainerVisible=resultsContainerCount===1&&await resultsContainer.isVisible();const ariaBusy=resultsContainerCount===1?await resultsContainer.getAttribute("aria-busy"):null;if(ariaBusy!==null&&ariaBusy!=="true"&&ariaBusy!=="false")throw new Error("SELECTOR_CONTRACT");const progressbarCount=await visibleCount(p.getByRole("progressbar"));const alertCount=await visibleCount(p.getByRole("alert"));const dialogCount=await visibleCount(p.getByRole("dialog"));${exhaustResultsScroll}`,
    `const rowLocators=resultsContainerCount===1?await resultsContainer.locator("li.artdeco-list__item:has(a[href*='/sales/lead/'])").all():[];if(rowLocators.length>30)throw new Error("SELECTOR_CONTRACT");for(const r of rowLocators){await r.scrollIntoViewIfNeeded();const marker=r.locator("[data-scroll-into-view]");if(await marker.count()!==1)throw new Error("SELECTOR_CONTRACT");const rawRowIdentity=await marker.getAttribute("data-scroll-into-view");if(typeof rawRowIdentity!=="string")throw new Error("SOURCE_MISMATCH");const rawIdentityMatch=/^urn:li:fs_salesProfile:(?:\\(([A-Za-z0-9_-]+),[^)]*\\)|([A-Za-z0-9_-]+))$/.exec(rawRowIdentity);const rowSalesLeadId=rawIdentityMatch?.[1]??rawIdentityMatch?.[2]??null;if(typeof rowSalesLeadId!=="string")throw new Error("SOURCE_MISMATCH");const rowIdentity="urn:li:fs_salesProfile:"+rowSalesLeadId;if(rowIdentitySet.has(rowIdentity))continue;const leads=r.locator("a[href*='/sales/lead/']");if(await leads.count()<1)throw new Error("SELECTOR_CONTRACT");const lead=leads.first();const href=await lead.getAttribute("href");if(typeof href!=="string")throw new Error("SOURCE_MISMATCH");const observedLeadUrl=new URL(href,"https://www.linkedin.com");const leadMatch=/^\\/sales\\/lead\\/([^/,]+)(?:,.*)?\\/?$/.exec(observedLeadUrl.pathname);const salesLeadId=leadMatch?.[1]??null;if(observedLeadUrl.origin!=="https://www.linkedin.com"||typeof salesLeadId!=="string"||!/^[A-Za-z0-9_-]+$/.test(salesLeadId)||salesLeadId!==rowSalesLeadId)throw new Error("SOURCE_MISMATCH");const salesLeadUrl="https://www.linkedin.com/sales/lead/"+salesLeadId;if(salesLeadSet.has(salesLeadUrl))continue;const personName=r.locator("[data-anonymize='person-name']").first();const name=String((await personName.count())>0?await personName.textContent():(await lead.textContent()??"")).replace(/\\s+/g," ").trim();if(!name)throw new Error("SOURCE_MISMATCH");${sourceRowEligibility()}rowIdentitySet.add(rowIdentity);salesLeadSet.add(salesLeadUrl);items.push({rowIdentity,salesLeadUrl,name});if(items.length===30)break;}`,
    `const namedPagination=p.getByRole("navigation",{name:"Pagination",exact:true});const namedPaginationCount=await namedPagination.count();const allPagination=p.getByRole("navigation");const allPaginationCount=await allPagination.count();if(namedPaginationCount>1||(namedPaginationCount===0&&allPaginationCount>1))throw new Error("SELECTOR_CONTRACT");const hasPaginationContainer=namedPaginationCount===1||allPaginationCount===1;const paginationLocator=hasPaginationContainer?(namedPaginationCount===1?namedPagination:allPagination.first()):null;const currentPageLocator=hasPaginationContainer?paginationLocator.locator("[aria-current='page']"):p.locator("[aria-current='page']");let currentPageCount=await currentPageLocator.count();if(currentPageCount>1)throw new Error("SELECTOR_CONTRACT");if(currentPageCount===1&&!(await currentPageLocator.isVisible()))currentPageCount=0;let cursorIdentity=null;if(currentPageCount===1){cursorIdentity=await currentPageLocator.getAttribute("aria-label");if(typeof cursorIdentity!=="string"||!/^Page [1-9][0-9]*$/.test(cursorIdentity))throw new Error("SELECTOR_CONTRACT");}const nextLocator=hasPaginationContainer?paginationLocator.getByRole("button",{name:"Next",exact:true}):p.getByRole("button",{name:"Next",exact:true});let nextControlCount=await nextLocator.count();if(nextControlCount>1)throw new Error("SELECTOR_CONTRACT");if(nextControlCount===1&&!(await nextLocator.isVisible()))nextControlCount=0;let nextDisabled=null;if(nextControlCount===1)nextDisabled=await nextLocator.isDisabled();const navigationCount=hasPaginationContainer||currentPageCount===1||nextControlCount===1?1:0;`,
    `const fullyLoaded=resultsContainerCount===1&&resultsContainerVisible&&ariaBusy!=="true"&&progressbarCount===0;const blockerFree=alertCount===0&&dialogCount===0;const pageIdentity=cursorIdentity===null?null:"salesnav-saved-search:"+sourceContract.savedSearchId+":"+cursorIdentity;pageEvidence={stateKey:${literal(WORKFLOW_STATE_KEYS.candidateResults)},url:sourceContract.searchUrl,resultsContainerCount,resultsContainerVisible,ariaBusy,progressbarCount,alertCount,dialogCount,fullyLoaded,blockerFree,cursorIdentity,pageIdentity};pagination={navigationCount,currentPageCount,nextControlCount,nextDisabled};if(items.length>=30||nextControlCount===0||nextDisabled===true)break;const cursorKey=cursorIdentity??observedPageUrl.href;if(visitedCursors.has(cursorKey))throw new Error("PAGINATION_STALLED");visitedCursors.add(cursorKey);if(await nextLocator.count()!==1||!(await nextLocator.isVisible())||await nextLocator.isDisabled())break;const previousCursor=cursorIdentity;const previousUrl=p.url();await nextLocator.click();let advanced=false;for(let waitAttempt=0;waitAttempt<45;waitAttempt+=1){await p.waitForTimeout(1000);const nextUrl=p.url();const nextCurrent=p.locator("[aria-current='page']");const nextCurrentCount=await nextCurrent.count();if(nextCurrentCount>1)throw new Error("SELECTOR_CONTRACT");let nextCursor=null;if(nextCurrentCount===1&&await nextCurrent.isVisible()){nextCursor=await nextCurrent.getAttribute("aria-label");if(typeof nextCursor!=="string"||!/^Page [1-9][0-9]*$/.test(nextCursor))throw new Error("SELECTOR_CONTRACT");}if(nextUrl!==previousUrl||nextCursor!==previousCursor){advanced=true;break;}}if(!advanced)throw new Error("PAGINATION_STALLED");`,
    `}`,
  ].join("");
  const evidence = `if(pageEvidence===null||pagination===null)throw new Error("SOURCE_CAPTURE_INCOMPLETE");const __logs=await getLatestLogs({page:p,sinceLastCall:true});const __diagnosticSummary=__selectDiagnostics(__logs);const stableRowIds=[...rowIdentitySet].sort();if(pageEvidence.fullyLoaded&&pageEvidence.blockerFree&&items.length>0&&reload!==null&&pagination.navigationCount===1&&pagination.currentPageCount===1&&pagination.nextControlCount===1&&pagination.nextDisabled===true&&pageEvidence.pageIdentity!==null&&pageEvidence.cursorIdentity!==null){const __crypto=require("node:crypto");const fingerprintBase={schemaVersion:1,kind:"network_source_terminal_fingerprint",sourceContractFingerprint:sourceContract.contractFingerprint,searchUrl:sourceContract.searchUrl,pageIdentity:pageEvidence.pageIdentity,cursorIdentity:pageEvidence.cursorIdentity,stableRowIds,rowCount:items.length,nextControl:"disabled"};const terminalFingerprint=__crypto.createHash("sha256").update(JSON.stringify(fingerprintBase)).digest("hex");terminalEvidence=Object.freeze({schemaVersion:1,kind:"network_source_terminal_observation",captureInvocationId:__INVOCATION_ID__,observedAt:capturedAt,sourceId:sourceContract.sourceId,sourceName:sourceContract.sourceName,savedSearchId:sourceContract.savedSearchId,searchUrl:sourceContract.searchUrl,sourceContractVersion:sourceContract.contractVersion,sourceContractFingerprint:sourceContract.contractFingerprint,terminalFingerprint,pageIdentity:pageEvidence.pageIdentity,cursorIdentity:pageEvidence.cursorIdentity,stableRowIds,rowCount:items.length,nextControl:"disabled",navigationInvocationId:reload.navigationInvocationId,reloadIdentity:reload.reloadIdentity,reloadGeneration:reload.reloadGeneration,navigatedAt:reload.navigatedAt});}const data={schemaVersion:1,kind:"network_source_capture",captureInvocationId:__INVOCATION_ID__,capturedAt,sourceContract,url:${literal(url)},items,reload,page:pageEvidence,pagination,...(terminalEvidence===undefined?{}:{terminalEvidence})};${progress("candidate_results_observed", "{rowCount:items.length,terminal:terminalEvidence!==undefined,pagesVisited}")}${after}${progress("logs_captured")}${result("capture-candidate-results", "data")}`;
  return `${before}${exactPage(WORKFLOW_STATE_KEYS.candidateResults, url)}const sourceContract=${literal(sourceContract)};const capturedAt=new Date().toISOString();const captureState=state[${literal(SOURCE_CAPTURE_STATE_KEY)}];if(captureState!==undefined&&(!captureState||captureState.schemaVersion!==1||captureState.kind!=="network_source_capture_state"||typeof captureState.bySource!=="object"||captureState.bySource===null))throw new Error("SOURCE_MISMATCH");const staged=captureState?.bySource?.[sourceContract.sourceId]??null;if(staged!==null&&(staged.schemaVersion!==1||staged.kind!=="network_source_reload"||staged.sourceId!==sourceContract.sourceId||staged.searchUrl!==sourceContract.searchUrl||staged.sourceContractFingerprint!==sourceContract.contractFingerprint||typeof staged.navigationInvocationId!=="string"||staged.reloadIdentity!==staged.navigationInvocationId+":reload"||!Number.isSafeInteger(staged.reloadGeneration)||staged.reloadGeneration<1||typeof staged.navigatedAt!=="string"))throw new Error("SOURCE_MISMATCH");const reload=staged===null?null:{navigationInvocationId:staged.navigationInvocationId,reloadIdentity:staged.reloadIdentity,reloadGeneration:staged.reloadGeneration,navigatedAt:staged.navigatedAt};const items=[];const rowIdentitySet=new Set();const salesLeadSet=new Set();const visitedCursors=new Set();let pageEvidence=null;let pagination=null;let terminalEvidence;let pagesVisited=0;${sourcePage}${evidence}`;
}

function sentListCaptureBody(sentUrl: string): string {
  const body = [
    'const workspace=p.locator("main#workspace").first();const workspaceText=String(await workspace.textContent({timeout:10000})??"").replace(/\\s+/g," ").trim();const peopleMatch=/People \\(([\\d,]+)\\)/.exec(workspaceText);if(!peopleMatch)throw new Error("SELECTOR_CONTRACT");const peopleCount=Number(peopleMatch[1].replace(/,/g,""));if(!Number.isSafeInteger(peopleCount)||peopleCount<0)throw new Error("SELECTOR_CONTRACT");',
    'const controls=p.locator("[aria-label^=\'Withdraw invitation sent to \']");const names=[];const nameSet=new Set();const identitySet=new Set();const identities=[];const collectRows=async()=>{const extracted=await controls.evaluateAll((nodes)=>{const clean=(value)=>String(value||"").replace(/\\s+/g," ").trim();const normalizePublicUrl=(value)=>{try{const parsed=new URL(String(value||""),"https://www.linkedin.com");if(!["linkedin.com","www.linkedin.com"].includes(parsed.hostname))return null;const parts=parsed.pathname.split("/").filter(Boolean);if(parts.length<2||parts[0]!=="in")return null;return "https://www.linkedin.com/in/"+encodeURIComponent(decodeURIComponent(parts[1])).toLowerCase();}catch{return null;}};const salesLeadId=(value)=>{try{const parsed=new URL(String(value||""),"https://www.linkedin.com");if(!["linkedin.com","www.linkedin.com"].includes(parsed.hostname))return null;const parts=parsed.pathname.split("/").filter(Boolean);if(parts.length<3||parts[0]!=="sales"||parts[1]!=="lead")return null;const id=decodeURIComponent(parts[2]||"").trim();return id||null;}catch{return null;}};const invitationRow=(control)=>{if(!control)return null;const declared=control.closest("li")||control.closest("[data-view-name]")||control.closest("[data-chameleon-result-urn]");if(declared)return declared;for(let node=control.parentElement;node&&node!==document.body;node=node.parentElement){if(node.querySelector("a[href*=\'/in/\'], a[href*=\'/sales/lead/\']"))return node;}return control.parentElement;};const findUrn=(root,prefix)=>{if(!root)return null;const nodes=[root,...root.querySelectorAll("*")];const escaped=prefix.replace(/[.*+?^${}()|[\\]\\\\]/g,"\\\\$&");const urnPattern=new RegExp(escaped+"[^)\\"\'\\\\s<>,]*");for(const node of nodes){for(const attr of Array.from(node.attributes||[])){const value=clean(attr.value);const match=value.match(urnPattern);if(match)return match[0];}}return null;};const rows=[];for(const control of nodes){const label=clean(control.getAttribute("aria-label"));const prefix="Withdraw invitation sent to ";if(!label.startsWith(prefix))continue;const name=clean(label.slice(prefix.length));if(!name)continue;const row=invitationRow(control);const hrefs=[];if(control.getAttribute("href"))hrefs.push(control.getAttribute("href"));if(row){for(const anchor of row.querySelectorAll("a[href*=\'/in/\'], a[href*=\'/sales/lead/\']"))hrefs.push(anchor.getAttribute("href"));}let identity=null;for(const href of hrefs){const publicUrl=normalizePublicUrl(href);if(publicUrl){identity=publicUrl;break;}const leadId=salesLeadId(href);if(leadId){identity=leadId;break;}}if(!identity){const invitationUrn=findUrn(row,"urn:li:fsd_invitation:");const profileUrn=findUrn(row,"urn:li:fsd_profile:");identity=invitationUrn||profileUrn||null;}rows.push({name,identity});}return rows;});for(const row of extracted){if(!nameSet.has(row.name)){nameSet.add(row.name);names.push(row.name);}if(typeof row.identity==="string"&&row.identity.trim()&&!identitySet.has(row.identity)){identitySet.add(row.identity);identities.push(row.identity);}}};for(let waitAttempt=0;waitAttempt<15&&(await controls.count())===0;waitAttempt+=1)await p.waitForTimeout(1000);',
    'const MAX_SCROLL_PASSES=200;const MAX_STAGNANT=12;const MAX_SENT_NAMES=2500;let loadMoreClicks=0;let scrollPasses=0;let stagnant=0;let exhausted=false;await collectRows();for(;scrollPasses<MAX_SCROLL_PASSES&&names.length<MAX_SENT_NAMES&&!exhausted;scrollPasses+=1){const beforeNames=nameSet.size;const beforeCount=await controls.count();const loadMore=p.getByRole("button",{name:"Load more",exact:true});const loadMoreCount=await loadMore.count();if(loadMoreCount>1)throw new Error("SELECTOR_CONTRACT");let moved=false;if(loadMoreCount===1&&await loadMore.isVisible()&&!(await loadMore.isDisabled())){await loadMore.click();loadMoreClicks+=1;moved=true;await p.waitForTimeout(1000);}await p.evaluate(()=>{const targets=[document.querySelector("main#workspace"),document.scrollingElement,document.documentElement,document.body,document.querySelector(".scaffold-finite-scroll__content")].filter(Boolean);for(const el of targets){try{el.scrollTo(0,el.scrollHeight);}catch{}try{el.scrollTop=el.scrollHeight;}catch{}}try{window.scrollTo(0,document.body.scrollHeight);}catch{}});await p.waitForTimeout(900);await collectRows();const afterCount=await controls.count();if(nameSet.size>beforeNames||afterCount>beforeCount){stagnant=0;moved=true;}else{stagnant+=1;if(stagnant>=MAX_STAGNANT){exhausted=true;break;}}if(names.length>=MAX_SENT_NAMES){exhausted=true;break;}if(names.length>=peopleCount&&peopleCount>0){exhausted=true;break;}}',
    "await collectRows();const controlCount=await controls.count();const contradictoryEvidence=names.length>peopleCount;const complete=!contradictoryEvidence&&(exhausted||(peopleCount>0&&names.length>=peopleCount));const competingSenderAbsent=false;const data={peopleCount,identities,names,complete,competingSenderAbsent,contradictoryEvidence};" +
      progress(
        "sent_list_observed",
        "{peopleCount,controlCount,namesLoaded:names.length,identitiesLoaded:identities.length,loadMoreClicks,scrollPasses,stagnant,exhausted,complete,bounded:true}",
      ) +
      after +
      finish("capture-sent-list", "data"),
  ];
  return `${before}${exactPage(WORKFLOW_STATE_KEYS.sentInvitations, sentUrl)}${body.join("")}`;
}

export function compileNetworkScript(
  command: NetworkCommand,
  input: NetworkScriptInput = {},
): CompiledScriptDescriptor<NetworkCommand> {
  const c = input.candidate;
  if (c) assertCandidateIdentity(c);
  if (input.sendPreparation !== undefined) {
    assertSendPreparationReceipt(input.sendPreparation);
    if (c && !same(c, input.sendPreparation.candidate))
      throw new TypeError("send preparation candidate mismatch");
  }
  if (input.sourceContract !== undefined) assertNetworkSourceContract(input.sourceContract);
  if (
    input.sourceContract !== undefined &&
    command !== "navigate-candidate-results" &&
    command !== "capture-candidate-results" &&
    command !== "walk-list"
  )
    throw new TypeError(
      "source contract is only valid for source navigation, capture, and walk-list",
    );
  const needCandidate = () => {
    if (!c) throw new TypeError(`${command} requires candidate identity`);
    return c;
  };
  const searchUrl = c?.searchUrl ?? input.url;
  const needSearch = () => {
    if (!searchUrl) throw new TypeError("candidate-results URL required");
    assertAllowedWorkflowUrl("candidateResults", searchUrl);
    return searchUrl;
  };
  const needSourceContract = () => {
    const resolved = resolveNetworkSourceContract(needSearch());
    if (input.sourceContract !== undefined && !same(input.sourceContract, resolved))
      throw new TypeError("source contract mismatch");
    return resolved;
  };
  const sentUrl = "https://www.linkedin.com/mynetwork/invitation-manager/sent/";

  switch (command) {
    case "navigate-candidate-results": {
      const u = needSearch();
      const sourceContract = needSourceContract();
      return networkIssue(
        command,
        "navigate",
        [
          "observation_before",
          "navigation_started",
          "navigation_returned",
          "observation_after",
          "logs_captured",
        ],
        sourceNavigationBody(u, sourceContract),
        undefined,
        undefined,
        sourceContract,
      );
    }
    case "capture-candidate-results": {
      const u = needSearch();
      const sourceContract = needSourceContract();
      return networkIssue(
        command,
        "none",
        ["observation_before", "candidate_results_observed", "observation_after", "logs_captured"],
        sourceCaptureBody(u, sourceContract),
        undefined,
        undefined,
        sourceContract,
      );
    }
    case "capture-candidate": {
      const x = needCandidate();
      return networkIssue(
        command,
        "none",
        ["observation_before", "candidate_observed", "observation_after", "logs_captured"],
        `${before}${exactPage(WORKFLOW_STATE_KEYS.candidateResults, x.searchUrl)}${candidateRow(x)}${progress("candidate_observed")}${after}${finish(command, `data:{candidate:${literal(x)},observationId:__INVOCATION_ID__+":candidate"}`)}`,
        x,
      );
    }
    case "click-connect-menu-item": {
      const x = needCandidate();
      return networkIssue(
        command,
        "connect",
        [
          "observation_before",
          "connect_started",
          "connect_returned",
          "observation_after",
          "logs_captured",
        ],
        connectCandidate(x),
        x,
      );
    }
    case "observe-connect-modal": {
      const x = needCandidate();
      return networkIssue(
        command,
        "none",
        ["observation_before", "modal_observed", "observation_after", "logs_captured"],
        `${before}${exactPage(WORKFLOW_STATE_KEYS.candidateResults, x.searchUrl)}${visibleDialogCount()}${progress("modal_observed")}${after}${finish(command, `data:{candidate:${literal(x)},modal:true,observationId:__INVOCATION_ID__+":modal"}`)}`,
        x,
      );
    }
    case "click-send":
      throw new TypeError("click-send was removed; use prepareNetworkSend then commitNetworkSend");
    case "prepare-send": {
      const x = needCandidate();
      const attemptId = input.attemptId;
      if (typeof attemptId !== "string" || !ATTEMPT_ID.test(attemptId))
        throw new TypeError("prepare-send requires a valid attemptId");
      return networkIssue(
        command,
        "none",
        ["observation_before", "send_prepared", "observation_after", "logs_captured"],
        `${before}${exactPage(WORKFLOW_STATE_KEYS.candidateResults, x.searchUrl)}${candidateRow(x)}${visibleSendControl()}const __crypto=require("node:crypto");const preparedAt=new Date().toISOString();const expiresAt=new Date(Date.parse(preparedAt)+${SEND_PREPARATION_TTL_MS}).toISOString();const token=__crypto.randomBytes(16).toString("hex");const preparationBase={schemaVersion:1,kind:"playwriter_network_send_preparation",prepareInvocationId:__INVOCATION_ID__,sessionId:__SESSION_ID__,attemptId:${literal(attemptId)},candidate:${literal(x)},preparedAt,expiresAt,token,page:{stateKey:${literal(SEND_PREPARATION_TARGET.page.stateKey)},url:${literal(x.searchUrl)}},modal:${literal(SEND_PREPARATION_TARGET.modal)},control:${literal(SEND_PREPARATION_TARGET.control)}};const fingerprint=__crypto.createHash("sha256").update(JSON.stringify(preparationBase)).digest("hex");const preparation=Object.freeze({...preparationBase,fingerprint});state[${literal(SEND_PREPARATION_STATE_KEY)}]=preparation;const receipt=Object.freeze({schemaVersion:1,kind:"network_send_prepared",receiptId:"pwprep:"+__INVOCATION_ID__+":"+token+":"+fingerprint,attemptId:${literal(attemptId)},preparedAt,candidate:${literal(x)}});${progress("send_prepared", "{receiptId:receipt.receiptId,attemptId:receipt.attemptId,fingerprint}")}${after}${finish(command, "data:receipt")}`,
        x,
      );
    }
    case "commit-send": {
      const receipt = input.sendPreparation;
      if (receipt === undefined)
        throw new TypeError("commit-send requires send preparation receipt");
      const x = needCandidate();
      const id = parseSendPreparationReceiptId(receipt.receiptId);
      const commitSource = [
        `${before}const receipt=${literal(receipt)};const __crypto=require("node:crypto");const expiresAt=new Date(Date.parse(receipt.preparedAt)+${SEND_PREPARATION_TTL_MS}).toISOString();const expectedBase={schemaVersion:1,kind:"playwriter_network_send_preparation",prepareInvocationId:${literal(id.prepareInvocationId)},sessionId:__SESSION_ID__,attemptId:receipt.attemptId,candidate:receipt.candidate,preparedAt:receipt.preparedAt,expiresAt,token:${literal(id.token)},page:{stateKey:${literal(SEND_PREPARATION_TARGET.page.stateKey)},url:receipt.candidate.searchUrl},modal:${literal(SEND_PREPARATION_TARGET.modal)},control:${literal(SEND_PREPARATION_TARGET.control)}};const computedFingerprint=__crypto.createHash("sha256").update(JSON.stringify(expectedBase)).digest("hex");if(computedFingerprint!==${literal(id.fingerprint)})throw new Error("PREPARATION_MISMATCH");const expected={...expectedBase,fingerprint:${literal(id.fingerprint)}};const stored=state[${literal(SEND_PREPARATION_STATE_KEY)}];if(!stored||JSON.stringify(stored)!==JSON.stringify(expected))throw new Error("PREPARATION_MISMATCH");const commitAt=new Date().toISOString();if(Date.parse(commitAt)<Date.parse(receipt.preparedAt)||Date.parse(commitAt)>Date.parse(expiresAt))throw new Error("PREPARATION_STALE");`,
        `${exactPage(WORKFLOW_STATE_KEYS.candidateResults, x.searchUrl)}${candidateRow(x)}${visibleSendControl()}${progress("send_commit_started", "{receiptId:receipt.receiptId,attemptId:receipt.attemptId}")}await send.click();delete state[${literal(SEND_PREPARATION_STATE_KEY)}];${progress("send_click_dispatched", "{receiptId:receipt.receiptId,attemptId:receipt.attemptId}")}`,
        `${postSendPageValidation("receipt.candidate")}${postSendPendingEvidence()}${postSendEvidence}`,
        `const postClickEvidence={observedUrl:observedPostSendUrl,modalCount,sendControlCount,pendingCount,capturedAt:new Date().toISOString()};${progress("send_post_click_observed", "postClickEvidence")}${after}${finish(command, 'data:{schemaVersion:1,kind:"network_send_commit",receiptId:receipt.receiptId,attemptId:receipt.attemptId,candidate:receipt.candidate,clickDispatched:true,postClickEvidence}')}`,
      ].join("");
      return networkIssue(
        command,
        "send",
        [
          "observation_before",
          "send_commit_started",
          "send_click_dispatched",
          "send_post_click_observed",
          "observation_after",
          "logs_captured",
        ],
        commitSource,
        x,
        receipt,
      );
    }
    case "observe-post-send": {
      const x = needCandidate();
      const observeSource = `${before}const p=state[${literal(WORKFLOW_STATE_KEYS.candidateResults)}];if(!p)throw new Error("WORKFLOW_PAGE_MISSING");${postSendPageValidation(literal(x))}let pendingCount=0;if(isCandidateResultsPage){${candidateRow(x)}pendingCount=await row.getByText("Pending",{exact:true}).count();}${progress("post_send_observed")}${after}${finish(command, `data:{candidate:${literal(x)},pendingCount,observationId:__INVOCATION_ID__+":post-send"}`)}`;
      return networkIssue(
        command,
        "none",
        ["observation_before", "post_send_observed", "observation_after", "logs_captured"],
        observeSource,
        x,
      );
    }
    case "navigate-sent-list":
      return networkIssue(
        command,
        "navigate",
        [
          "observation_before",
          "navigation_started",
          "navigation_returned",
          "observation_after",
          "logs_captured",
        ],
        `${before}${progress("navigation_started")}${usablePage(WORKFLOW_STATE_KEYS.sentInvitations)}await p.goto(${literal(sentUrl)},{waitUntil:"domcontentloaded"});${progress("navigation_returned")}if(p.url()!==${literal(sentUrl)})throw new Error("WRONG_PAGE");${after}${finish(command, "data:{url:p.url()}")}`,
      );
    case "capture-sent-list":
      return networkIssue(
        command,
        "none",
        ["observation_before", "sent_list_observed", "observation_after", "logs_captured"],
        sentListCaptureBody(sentUrl),
      );
    case "walk-list": {
      const u = needSearch();
      const sourceContract = needSourceContract();
      const budget = input.budget;
      const pacingMs = input.pacingMs;
      if (!Number.isSafeInteger(budget) || (budget as number) < 1 || (budget as number) > 30)
        throw new TypeError("walk-list requires budget 1..30");
      if (!Number.isSafeInteger(pacingMs) || (pacingMs as number) < 0)
        throw new TypeError("walk-list requires non-negative pacingMs");
      return networkIssue(
        command,
        "send",
        [
          "observation_before",
          "navigation_started",
          "navigation_returned",
          "observation_after",
          "logs_captured",
        ],
        walkListBody(u, sourceContract, budget as number, pacingMs as number),
        undefined,
        undefined,
        sourceContract,
      );
    }
  }
}

export function compileAnalyticsScript(
  config: AnalyticsCommandConfig,
): CompiledScriptDescriptor<AnalyticsPlaywriterCommand> {
  const template = buildAnalyticsPlaywriterScript(config);
  return issue({
    command: template.command,
    action: template.action,
    phases: template.phases,
    source: prelude + template.source,
    definitionId: template.definitionId,
  });
}

export function materializeCompiledScript(
  descriptor: CompiledScriptDescriptor,
  invocationId: string,
  sessionId: number,
  progressPath: string,
  handoffNonce: string,
): string {
  return descriptor.source
    .replaceAll("__INVOCATION_ID__", literal(invocationId))
    .replaceAll("__SESSION_ID__", literal(sessionId))
    .replaceAll("__PROGRESS_PATH__", literal(progressPath))
    .replaceAll("__HANDOFF_NONCE__", literal(handoffNonce))
    .replaceAll("__COMMAND__", literal(descriptor.command))
    .replaceAll("__CANDIDATE__", literal(descriptor.candidate ?? null));
}
