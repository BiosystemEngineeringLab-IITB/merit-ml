/* MERIT-ML guided walkthrough. Presentation only: never submit forms or change scientific inputs. */
(() => {
  'use strict';
  if (window.MeritTour) return;
  // Stable across software releases; an update must not restart a visitor's completed tour.
  const KEY = 'merit-ml-guided-tour-v1', CHANNEL = 'merit-guided-tour', doc = document;
  let active = null, launcher, timer, lastFocus, originalTab, originalY = 0, restorers = [], pending = false;
  let memory = {}, savedWith = null;
  try { memory = window.top.__meritTourPreferences || {}; } catch (_) {}
  const visible = e => {
    if (!e || !e.isConnected || !e.getClientRects().length) return false;
    for (let p = e; p; p = p.parentElement) {
      if (p.hidden || getComputedStyle(p).display === 'none' || getComputedStyle(p).visibility === 'hidden') return false;
      if (p !== e && p.tagName === 'DETAILS' && !p.open && !p.querySelector(':scope > summary')?.contains(e)) return false;
    }
    const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0;
  };
  const q = (s, root = doc) => [...root.querySelectorAll(s)].find(visible) || null;
  function preferences() {
    for (const store of ['localStorage', 'sessionStorage']) {
      try { const v = JSON.parse(window[store].getItem(KEY)); if (v && typeof v === 'object' && !Array.isArray(v)) return v; } catch (_) {}
    }
    return memory;
  }
  function save(patch) {
    const v = {...preferences(), ...patch, updated: new Date().toISOString()}; memory = v;
    try { window.top.__meritTourPreferences = v; } catch (_) {}
    for (const store of ['localStorage', 'sessionStorage']) try { window[store].setItem(KEY, JSON.stringify(v)); if (!savedWith) savedWith = store; } catch (_) {}
    return v;
  }
  function frame() { return doc.getElementById('report'); }
  function context() {
    if (doc.querySelector('.v9-rd,.v9-rec') || window.__MERIT_STATE_JSON) return 'report';
    if (doc.querySelector('#sid') && !doc.body.classList.contains('report-ready') && !doc.body.classList.contains('assessment-active')) return 'landing';
    return null;
  }
  function rootReport() { return q('.v9-rd'); }
  function overview() {
    const b = [...doc.querySelectorAll('.tab-btn')].find(e => visible(e) && e.textContent.trim() === 'Overview');
    if (b && !b.classList.contains('active')) b.click();
  }
  function tab(name) { const b = [...doc.querySelectorAll('.tab-btn')].find(e => visible(e) && e.textContent.trim() === name); if (b) b.click(); }
  function opened(e) { if (e && !e.open) { e.open = true; restorers.push(() => { if (e.isConnected) e.open = false; }); } return e; }
  function revealPanel(buttonSelector, panelSelector) {
    const r = rootReport(), b = r?.querySelector(buttonSelector), p = r?.querySelector(panelSelector);
    if (b && p && p.hidden) { b.click(); restorers.push(() => { if (p.isConnected && !p.hidden) b.click(); }); }
    return p;
  }
  function firstMore(column) {
    const r = rootReport()?.querySelector('.v9-rd-row');
    const c = r?.children[column]; opened(c?.querySelector('details.v9-rd-more')); return c;
  }
  function textElement(words) {
    return [...doc.querySelectorAll('h2,h4,p,div')].filter(e => visible(e) && e.textContent.trim().startsWith(words) && e.textContent.length < 1800).sort((a,b)=>a.textContent.length-b.textContent.length)[0] || null;
  }
  const landing = [
    {id:'welcome', title:'Welcome to MERIT-ML', target:() => q('.tagline'), body:'This short tour explains how to assess a public Metabolomics Workbench study and read the result. You can use Back, Next or Skip at any time. It does not start a calculation or change any setting.'},
    {id:'search', title:'Start with a study accession', target:() => q('#start'), body:'Enter a Workbench study ID such as ST000001, then choose Search. MERIT retrieves the available tabular endpoints and metadata and assesses each analysis × endpoint separately. Availability of a study does not guarantee that it contains a supported quantitative matrix.'},
    {id:'progress', title:'What happens after Search', target:() => q('#start'), body:'A progress bar names the calculation steps. Generating report follows the calculations and stays visible until the report opens. The assessment can take longer for large studies or slow repository responses. You do not need to keep this tour open.'},
    {id:'recent', title:'Choose a recent study instead', target:() => q('.latest'), body:'Recent-study cards show the accession, title, release date and organism. Selecting an available card fills the search and starts that study when the application is ready. These cards are not a ranking of readiness. If the list is unavailable, use the study-ID search or the repository link.'},
    {id:'resources', title:'Browser limits and the full application', target:() => q('.notice'), body:'The browser edition handles one study at a time. A resource-intensive matrix may be left unassessed with its reason while other supported matrices continue. This is not a score of zero or proof that the file is absent. The linked Docker application uses your computer for larger studies and bulk work.'},
    {id:'bulk', title:'Bulk assessment', target:() => q('.bulk-entry'), body:'In the full application, paste study IDs, check availability, and run the available studies. Individual study reports remain interactive. The browser edition disables bulk execution and points to Docker. These are resource and workflow differences, not different meanings of the metrics.'},
    {id:'help', title:'Methods, source code and replay', target:() => q('.nav'), body:'About explains the scope; Publication links to the methods; GitHub provides the full application. The light/dark switch changes display only. Use Take a tour to replay this guidance later. Completion and opt-out are saved in this browser for this site; they are not tied to a study.'}
  ];
  const report = [
    {id:'report',title:'Read one matrix at a time',target:() => textElement('MERIT-ML Assessment Report'),body:'This report covers the study named in the heading. A matrix means one analysis read from one endpoint: datatable, mwTab or untarg_data. MERIT does not merge these into one study score or certify predictive performance. The profile states specific conditions and findings.'},
    {id:'alignment',title:'Extra datatable rows are disclosed',target:() => textElement('Tier 1 sample alignment'),body:'When shown, this notice identifies all-zero datatable rows absent from the paired mwTab block. Rows meeting the alignment rule are excluded from measured-sample counts. Read the listed analysis, positions and identifiers before assuming two endpoints measure different numbers of samples.'},
    {id:'sources',title:'Verify context and available sources',target:() => q('.v9-rec')?.nextElementSibling,body:'The study card gives identity, factors, class sizes, source availability, eligible samples, feature counts and source-verification links. Differing endpoint values remain visible. Recorded context and derived context are informational; neither is an overall judgement of the original study.'},
    {id:'glance',title:'Start with Data at a glance',target:() => {const g=q('.v9-rec');opened(g);opened(g?.querySelector('.v9-rec-card'));return g?.querySelector('.v9-rec-card') || g;},body:'Open each analysis card for a plain-language explanation of its named source matrix: samples and repeated positions, feature labels, class support, missing/zero entries, other analytical findings and cautions. The narrative follows the active inputs. It describes mwTab (or datatable when mwTab is unavailable) and untarg_data separately; the profile below covers every endpoint.'},
    {id:'profile',title:'The readiness profile has separate parts',target:rootReport,body:'The summary combines literal class-count support and analytical alerts, with the number of checks assessed. Annotation and metadata are shown separately. A typical value in one metric cannot offset a finding in another. There is no universal 0–100 readiness total and no automatic “ML-ready” certificate.'},
    {id:'definitions',title:'More than just a score range',target:() => {const d=rootReport()?.querySelector('details');opened(d);return d;},body:'Score descriptions define the count condition, reference R1, low/extreme alerts, exact cut-offs, unavailable checks and the limits of the summary. An alert prompts review; no alert does not mean that a matrix has no problem. A high individual value has the meaning specified for that metric.'},
    {id:'classes',title:'Choose the intended classification task',target:() => revealPanel('.v9-rd-open','.v9-rd-classes'),body:'The default class is the entire deposited factor string. The editor lets you choose readable varying factors and observed classes. Candidate counts cover eligible IDs across the study; assessment counts are calculated separately for each matrix. Picking one factor pools over the others and changes the question. Apply classes only after making a scientifically meaningful choice.'},
    {id:'folds',title:'CV folds control the count requirement',target:() => rootReport()?.querySelector('.v9-rd-k'),body:'The CV k selector defaults to five folds. Counts are met when at least two selected classes are present and each has at least k distinct labelled ML-eligible sample IDs. This is a count condition, not a power calculation, an independence check or a guarantee of stable accuracy. CV k is different from the neighbour setting used for separability.'},
    {id:'counts',title:'Read the actual counts and task diagnostics',target:() => firstMore(3),body:'Show more gives the smallest class and required minimum, classes, included samples, features, p/n, Class support and separability with chance level where assessed. Not met means the count condition fails; Cannot assess means the selected classes cannot be evaluated here. Separability describes local overlap, not model accuracy.'},
    {id:'subjects',title:'Verify shared subject identifiers',target:() => q('.v9-rd-subj'),body:'Several sample IDs can belong to one subject. Deposited subject values can also be a species or group name, so verify them before confirming. When they are real repeated subjects, keep each subject on one side of training/test splits. Meeting the sample-count condition does not establish that subject-disjoint folds are feasible.'},
    {id:'alerts',title:'An alert names a property to review',target:() => firstMore(4),body:'The four checks are Missing entries and zeros, Low-information features, Redundant features and sample-median Outliers. Show more translates scores into cell, feature or position burdens and identifies which checks could not run. Zeros are not automatically missing, correlated features do not prevent every kind of model, and a median outlier can reflect biology.'},
    {id:'cutoffs',title:'Reference thresholds and custom alerts',target:() => revealPanel('.v9-rd-cut-open','.v9-rd-cuts'),body:'By default, extreme and low alerts use the 2.5th and 10th percentiles of fixed reference R1. The displayed absolute cut-offs differ by metric. You can choose supported percentiles or enter a custom cut-off for a check and tier. These change alert decisions, not the underlying scores. They are screening conventions, not validated failure probabilities.'},
    {id:'coverage',title:'Assessed does not mean passed',target:() => rootReport()?.querySelector('.v9-rd-row')?.children[2],body:'4/4 checks assessed means all four could be computed. No alerts among 1/4 assessed is incomplete evidence, not four passes. The report names unavailable checks and their reasons. Browser resource restrictions, absent files and an unassessable metric are different situations; none should be read as a passing score.'},
    {id:'reuse',title:'Annotation and metadata have their own meaning',target:() => q('.v9-rd-ind'),body:'Named-feature coverage and RefMet mapping describe interpretability, not predictive ability. External identifiers, mass/RT fields and LC-method components describe documentation for reuse. Their denominators and applicability differ. These indicators are not averaged into the readiness summary; the detailed tabs preserve their evidence.'},
    {id:'grid',title:'Inspect every individual item',target:() => {const g=q('.v9-ovg');const b=g?.querySelector('.v9-ovg-btn');if(b && b.getAttribute('aria-pressed')!=='true'){b.click();restorers.push(()=>b.isConnected && b.click());}return g;},body:'The item grid preserves each analysis, endpoint and metric. Show numbers removes dependence on colour. Hover, tap or focus a tile for its value, supporting counts and definition; section links lead to full details. Hatched/unavailable and no-matrix states are explained in the legend. The colours are not the old readiness bands.'},
    {id:'tools',title:'Explore settings with Help and Defaults',target:() => {const b=doc.getElementById('tool-rail-toggle');if(b?.getAttribute('aria-expanded')==='false'){b.click();restorers.push(()=>b.isConnected && b.click());}return q('.v2-tune');},body:'Show tools groups the tunable parameters and gives Help, defaults and allowed ranges. These include low-information rules, correlation and median-outlier thresholds, QC RSD acceptance, alert cut-offs, and separability settings. Apply recomputes the relevant results. Defaults restores settings; it does not validate a study or change its deposited values.'},
    {id:'edits',title:'Inspect samples and confirm subjects carefully',target:() => {tab('Adjust Matrix Properties');return q('.v9-editor');},body:'Use as, labels, batch-like labels and subjects are separate inputs. An edit to an ID applies wherever that ID occurs; matrix-status strips and deposited evidence show the scope. Apply sample edits recalculates applicable results, and Clear all edits restores defaults. This tour has not edited, excluded or confirmed any sample.'},
    {id:'downloads',title:'Keep evidence and review before modelling',target:() => [...doc.querySelectorAll('button')].find(e=>visible(e)&&e.textContent.includes('Download MERIT-ML Assessment JSON')),body:'The assessment JSON retains the reported evidence and settings. Matrix downloads in Data at a glance name the source and include original positions and classes in force; rows outside chosen classes can have an empty class. Review missingness, preprocessing, label meaning, other factors, subjects and batch context before modelling. Fit learned preprocessing and feature selection within training folds.'}
  ];
  const style = `
  .merit-tour-launcher{font:600 12px/1.3 Arial,sans-serif!important;color:#096d69!important;background:transparent!important;border:1px solid #9abfbc!important;border-radius:18px!important;padding:7px 11px!important;cursor:pointer;white-space:nowrap}
  .merit-tour-standalone{position:fixed;left:18px;top:16px;z-index:2100;background:#fff!important}
  #merit-tour-root{position:fixed;inset:0;z-index:2147483000;font:15px/1.5 Arial,sans-serif;color:#183536;--tour-bg:#fff;--tour-ink:#183536;--tour-soft:#edf5f4;--tour-line:#b8cfcc;--tour-accent:#08766e;--tour-muted:#4b6867}
  html[data-theme="dark"] #merit-tour-root{--tour-bg:#17272b;--tour-ink:#edf6f5;--tour-soft:#21383b;--tour-line:#527473;--tour-accent:#8ddfd2;--tour-muted:#bed1cf;color:var(--tour-ink)}
  html[data-theme="dark"] .merit-tour-launcher{color:#ace4d9!important;border-color:#62928b!important;background:#18302b!important}
  #merit-tour-root *{box-sizing:border-box}#merit-tour-root .merit-tour-shade{position:fixed;background:rgba(9,25,32,.52);pointer-events:auto}
  #merit-tour-root .merit-tour-highlight{position:fixed;border:3px solid #25c1ae;border-radius:8px;pointer-events:none;box-shadow:0 0 0 2px #fff8}
  #merit-tour-root .merit-tour-card{position:fixed;width:min(430px,calc(100vw - 24px));max-height:calc(100dvh - 28px);overflow:auto;overscroll-behavior:contain;background:var(--tour-bg);color:var(--tour-ink);border:1px solid var(--tour-line);border-radius:14px;box-shadow:0 14px 60px #0006;padding:19px;text-align:left}
  #merit-tour-root h2{font:700 20px/1.3 Arial,sans-serif;color:var(--tour-ink);margin:5px 25px 12px 0}#merit-tour-root p{font:15px/1.5 Arial,sans-serif;color:var(--tour-ink);margin:8px 0 14px}
  #merit-tour-root button{font:600 13px/1.4 Arial,sans-serif;padding:8px 12px;border:1px solid var(--tour-line);border-radius:8px;background:var(--tour-soft);color:var(--tour-ink);cursor:pointer;min-height:36px}#merit-tour-root button:focus-visible,#merit-tour-root input:focus-visible,.merit-tour-launcher:focus-visible{outline:3px solid #21b3a1;outline-offset:3px}
  #merit-tour-root .merit-tour-next{background:var(--tour-accent);color:var(--tour-bg);border-color:var(--tour-accent)}#merit-tour-root button:disabled{opacity:.45;cursor:default}#merit-tour-root .merit-tour-close{position:absolute;right:10px;top:10px;padding:2px 10px;font-size:23px;border:0;background:transparent}
  #merit-tour-root .merit-tour-actions{display:flex;gap:7px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-top:12px}#merit-tour-root .merit-tour-progress{color:var(--tour-muted);font-size:12px;padding-right:25px}
  #merit-tour-root .merit-tour-pref{display:flex;gap:8px;align-items:flex-start;font-size:12px;color:var(--tour-muted);margin-top:14px;text-transform:none;letter-spacing:normal;font-weight:400}#merit-tour-root input{width:16px;height:16px;margin:2px 0 0;flex:none;accent-color:var(--tour-accent)}#merit-tour-root small{display:block;color:var(--tour-muted);font-size:11px;margin-top:7px}
  .browser-mode.report-ready .report-frame,.browser-mode .report-frame.loading{top:var(--merit-tour-topbar-height,58px)!important;height:calc(100dvh - var(--merit-tour-topbar-height,58px))!important}
  @media(max-width:600px){#merit-tour-root .merit-tour-card{max-height:62dvh;padding:15px}#merit-tour-root p{font-size:14px} .topbar .nav{gap:10px;flex-wrap:wrap}.topbar:has(.merit-tour-launcher){min-height:58px;height:auto}.merit-tour-launcher{font-size:11px!important;padding:6px 8px!important}}
  `;
  let overlay, card, shades, highlight, heading, body, counter, back, next, opt;
  function element(tag,cls,text,parent) {const e=doc.createElement(tag);if(cls)e.className=cls;if(text)e.textContent=text;if(parent)parent.appendChild(e);return e;}
  function create() {
    overlay=element('div');overlay.id='merit-tour-root';shades=Array.from({length:4},()=>element('div','merit-tour-shade','',overlay));highlight=element('div','merit-tour-highlight','',overlay);
    card=element('section','merit-tour-card','',overlay);card.setAttribute('role','dialog');card.setAttribute('aria-modal','true');card.setAttribute('aria-labelledby','merit-tour-title');card.setAttribute('aria-describedby','merit-tour-description');card.tabIndex=-1;
    const close=element('button','merit-tour-close','×',card);close.type='button';close.setAttribute('aria-label','Skip tour');close.onclick=()=>stop('skipped');
    counter=element('div','merit-tour-progress','',card);counter.setAttribute('aria-live','polite');
    heading=element('h2','','',card);heading.id='merit-tour-title';body=element('p','','',card);body.id='merit-tour-description';
    const actions=element('div','merit-tour-actions','',card);back=element('button','','Back',actions);back.type='button';back.onclick=()=>go(active.index-1);
    const skip=element('button','','Skip tour',actions);skip.type='button';skip.onclick=()=>stop('skipped');next=element('button','merit-tour-next','Next',actions);next.type='button';next.onclick=()=>active.index+1<active.steps.length?go(active.index+1):stop('completed');
    const label=element('label','merit-tour-pref','',card);opt=element('input','','',label);opt.type='checkbox';opt.checked=!!preferences().optOut;label.appendChild(doc.createTextNode('Don’t show tours automatically'));opt.onchange=()=>save({optOut:opt.checked});
    element('small','',savedWith==='localStorage'?'Remembered in this browser for this site. Take a tour can always replay it. Clearing site data resets this preference.':savedWith==='sessionStorage'?'Remembered for this browser session; persistent storage is unavailable. Take a tour can always replay it.':'Browser storage is unavailable: preferences last only while this page remains open. Take a tour can replay it.',card);
    doc.body.appendChild(overlay);doc.addEventListener('keydown',keyboard,true);window.addEventListener('scroll',schedulePosition,{passive:true});window.addEventListener('resize',schedulePosition);
  }
  function keyboard(e) {
    if(!active)return;
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();stop('skipped');return;}
    if(e.key==='Tab'){const a=[...card.querySelectorAll('button,input')].filter(e=>visible(e)&&!e.disabled);const i=a.indexOf(doc.activeElement);if(e.shiftKey&&i<=0){e.preventDefault();a.at(-1)?.focus();}else if(!e.shiftKey&&(i<0||i===a.length-1)){e.preventDefault();a[0]?.focus();}}
  }
  let positionFrame;
  function schedulePosition(){if(!positionFrame)positionFrame=requestAnimationFrame(()=>{positionFrame=null;position();});}
  function position() {
    if(!active || !card || !active.target?.isConnected)return;
    const r=active.target.getBoundingClientRect(),W=innerWidth,H=innerHeight,pad=5;
    const x=Math.max(0,r.left-pad),y=Math.max(0,r.top-pad),right=Math.min(W,r.right+pad),bottom=Math.min(H,r.bottom+pad),w=Math.max(0,right-x),h=Math.max(0,bottom-y);
    [[0,0,W,y],[0,bottom,W,Math.max(0,H-bottom)],[0,y,x,h],[right,y,Math.max(0,W-right),h]].forEach((a,i)=>Object.assign(shades[i].style,{left:a[0]+'px',top:a[1]+'px',width:a[2]+'px',height:a[3]+'px'}));
    Object.assign(highlight.style,{left:x+'px',top:y+'px',width:w+'px',height:h+'px'});
    const cr=card.getBoundingClientRect(),cw=cr.width,ch=cr.height;let cx,cy;
    if(W<650){cx=(W-cw)/2;cy=H-ch-12;if(y>H*.52)cy=12;}
    else if(W-right>cw+24){cx=right+12;cy=Math.max(12,Math.min(y,H-ch-12));}
    else if(x>cw+24){cx=x-cw-12;cy=Math.max(12,Math.min(y,H-ch-12));}
    else {cx=Math.min(W-cw-12,Math.max(12,W-cw-20));cy=bottom+ch+24<H?bottom+12:(y>ch+24?y-ch-12:Math.max(12,H-ch-16));}
    Object.assign(card.style,{left:Math.max(12,cx)+'px',top:Math.max(12,cy)+'px'});
  }
  function restoreStep(){for(const f of restorers.reverse())try{f();}catch(_){}restorers=[];}
  function go(index) {
    if(!active)return;restoreStep();if(active.phase==='report')overview();active.index=index;
    let target;try{target=active.steps[index].target();}catch(_){}
    if(!target || !target.isConnected){active.steps.splice(index,1);if(!active.steps.length)return stop('completed');return go(Math.min(index,active.steps.length-1));}
    active.target=target;const step=active.steps[index];heading.textContent=step.title;body.textContent=step.body;counter.textContent=(active.phase==='landing'?'Search-page tour':'Report tour')+' · '+(index+1)+' of '+active.steps.length;
    overlay.dataset.phase=active.phase;overlay.dataset.step=step.id;back.disabled=index===0;next.textContent=index===active.steps.length-1?'Finish tour':'Next';card.scrollTop=0;
    const r=target.getBoundingClientRect();if(r.top<70||r.bottom>innerHeight-30)target.scrollIntoView({block:r.height>innerHeight*.6?'start':'center',behavior:'instant'});
    position();card.focus({preventScroll:true});schedulePosition();
  }
  function stop(result='skipped') {
    if(!active)return;save({[active.phase]:result});restoreStep();active=null;overlay?.remove();overlay=null;doc.removeEventListener('keydown',keyboard,true);window.removeEventListener('scroll',schedulePosition);window.removeEventListener('resize',schedulePosition);
    if(originalTab?.isConnected&&!originalTab.classList.contains('active'))originalTab.click();window.scrollTo({top:originalY,behavior:'instant'});
    if(lastFocus?.isConnected&&visible(lastFocus))lastFocus.focus({preventScroll:true});
    try{doc.dispatchEvent(new CustomEvent('merit:tour-closed'));}catch(_){}
  }
  function start(phase=context(),manual=false) {
    if(active || pending || !['landing','report'].includes(phase))return false;
    const pref=preferences();if(!manual&&(pref.optOut||pref[phase]))return false;
    // A parent's landing overlay must never cover a report or coexist with its iframe tour.
    if(phase==='report')try{if(window.parent!==window)window.parent.MeritTour?.stop('skipped');}catch(_){}
    lastFocus=doc.activeElement;originalY=scrollY;originalTab=q('.tab-btn.active');if(phase==='report')overview();
    let steps=(phase==='landing'?landing:report).filter(step=>{
      // These target functions reveal presentation-only panels; test existence without running them.
      const required={alignment:()=>!!textElement('Tier 1 sample alignment'),sources:()=>!!q('.v9-rec'),glance:()=>!!q('.v9-rec'),profile:()=>!!rootReport(),definitions:()=>!!rootReport(),classes:()=>!!rootReport()?.querySelector('.v9-rd-open'),folds:()=>!!rootReport(),counts:()=>!!rootReport()?.querySelector('.v9-rd-row'),subjects:()=>!!q('.v9-rd-subj'),alerts:()=>!!rootReport()?.querySelector('.v9-rd-row'),cutoffs:()=>!!rootReport()?.querySelector('.v9-rd-cut-open'),coverage:()=>!!rootReport()?.querySelector('.v9-rd-row'),reuse:()=>!!q('.v9-rd-ind'),grid:()=>!!q('.v9-ovg'),tools:()=>!!doc.getElementById('tool-rail-toggle'),edits:()=>[...doc.querySelectorAll('.tab-btn')].some(e=>visible(e)&&e.textContent.trim()==='Adjust Matrix Properties')};
      return required[step.id]?required[step.id]():true;
    });
    if(!steps.length)return false;
    save({[phase]:'seen'});active={phase,manual,steps,index:0};create();go(0);return true;
  }
  function replay(){
    if(active)return;
    const f=frame();if(doc.body.classList.contains('report-ready')&&f&&!f.hidden){try{if(f.contentWindow.MeritTour)return f.contentWindow.MeritTour.start('report',true);}catch(_){}return;}
    start(context(),true);
  }
  function installLauncher(){
    if(launcher||window.parent!==window)return;
    launcher=element('button','merit-tour-launcher','Take a tour');launcher.type='button';launcher.id='merit-tour-launcher';launcher.title='Replay guidance or change the automatic-tour preference';launcher.onclick=replay;
    const nav=doc.querySelector('nav.nav');if(nav)nav.insertBefore(launcher,nav.firstChild);else{launcher.classList.add('merit-tour-standalone');doc.body.appendChild(launcher);}
  }
  function assessAuto(){
    if(active){if(active.phase==='landing'&&context()!=='landing')stop('skipped');return;}
    const phase=context();if(!phase)return;const p=preferences();if(p.optOut||p[phase])return;
    if(doc.querySelector('.about:not([hidden]),#merit-about-modal:not([hidden])')||doc.querySelector('.merit-analytics-consent:not([hidden])'))return;
    if(phase==='report'&&window.parent!==window){try{const f=window.frameElement;if(!f||f.hidden||f.classList.contains('loading')||!window.parent.document.body.classList.contains('report-ready'))return;}catch(_){return;}}
    if(phase==='landing'&&q('#sid')?.value.trim())return;
    start(phase,false);
  }
  const api={start,replay,stop,preferences:()=>({...preferences()}),get active(){return active?{phase:active.phase,index:active.index,step:active.steps[active.index]?.id,count:active.steps.length}:null;},version:1};
  Object.defineProperty(window,'MeritTour',{value:api,writable:false});
  function init(){
    const s=element('style');s.id='merit-tour-style';s.textContent=style;doc.head.appendChild(s);installLauncher();
    doc.addEventListener('submit',()=>{if(active)stop('skipped');},true);
    window.addEventListener('storage',e=>{if(e.key===KEY&&active&&!active.manual&&preferences().optOut)stop('skipped');});
    // Observe only the parent/host state changes, never every content-node mutation in a large report.
    const mo=new MutationObserver(()=>{clearTimeout(timer);timer=setTimeout(assessAuto,250);});mo.observe(doc.body,{attributes:true,attributeFilter:['class']});
    if(window.parent!==window)try{mo.observe(window.parent.document.body,{attributes:true,attributeFilter:['class']});}catch(_){}
    doc.addEventListener('click',()=>{if(!active){clearTimeout(timer);timer=setTimeout(assessAuto,350);}},true);
    // A wrapped phone navigation bar must not overlap the report iframe.
    const bar=doc.querySelector('.topbar');
    if(bar && frame() && typeof ResizeObserver!=='undefined'){
      const size=new ResizeObserver(()=>doc.documentElement.style.setProperty('--merit-tour-topbar-height',bar.getBoundingClientRect().height+'px'));
      size.observe(bar);window.addEventListener('pagehide',()=>size.disconnect(),{once:true});
    }
    timer=setTimeout(assessAuto,650);
    window.addEventListener('pagehide',()=>{clearTimeout(timer);mo.disconnect();},{once:true});
  }
  if(doc.readyState==='loading')doc.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
