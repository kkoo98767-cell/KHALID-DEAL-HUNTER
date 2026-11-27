/* KHALID DEAL HUNTER — factual deal labels and Arabic archive UI. */
'use strict';
const ACTIVE_STORES=['Amazon.sa','Jarir'];
function ageHours(stamp,now=Date.now()){const d=Date.parse(stamp?.includes('T')?stamp:stamp?.replace(' ','T')+'Z');return Number.isFinite(d)&&now>=d?(now-d)/3600000:Infinity}
function dealView(p,now=Date.now()){
 const e=p.evidence||{},h=e.historical_savings||{},a=e.advertised_savings||{};
 const fresh=ageHours(p.last_seen,now)<=24;
 const validated=p.history_summary?.latest_validated===true;
 const valid=p.status!=='DATA_ANOMALY'&&p.deal_type!=='ANOMALY';
 const verified=e.verified_deal===true&&e.historical_evidence_valid===true&&fresh&&validated&&valid&&h.amount_sar>0&&h.percent>0;
 const potential=!verified&&fresh&&validated&&valid&&a.percent>=25;
 const featured=fresh&&validated&&valid&&(a.percent>=25||(e.historical_evidence_valid===true&&h.percent>=25));
 return {verified,potential,featured,fresh,validated,valid,percent:verified?h.percent:potential?a.percent:null,amount:verified?h.amount_sar:potential?a.amount_sar:null,score:!valid?0:!fresh||!validated?Math.min(e.trust_score||0,39):e.trust_score||0,status:verified?'موثوق بالسجل':potential?'عرض غير مؤكد':!valid?'يحتاج مراجعة':!fresh?'السعر يحتاج تحديثاً':'قيد الرصد',source:verified?'انخفاض تاريخي':potential?'خصم المتجر':'بدون خصم مثبت'};
}
function selectProducts(products,{view='offers',store='',search='',sort='discount',includeReview=false,favorites=new Set(),now=Date.now()}={}){
 const list=products.filter(p=>{if(!ACTIVE_STORES.includes(p.store))return false;const d=dealView(p,now);if(!includeReview&&!d.valid)return false;if(store&&p.store!==store)return false;if(!(`${p.title||''} ${p.brand||''}`).toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))return false;return view==='offers'?(d.featured||d.verified):view==='verified'?d.verified:view==='favorites'?favorites.has(p.id):true});
 const value=(p)=>{const d=dealView(p,now);return sort==='discount'?d.percent??-1:sort==='trust'?d.score:sort==='savings'?(d.verified?d.amount||0:0):sort==='price'?-p.price:Date.parse(p.last_seen?.includes('T')?p.last_seen:p.last_seen?.replace(' ','T')+'Z')||0};
 return list.sort((a,b)=>value(b)-value(a)||String(a.id).localeCompare(String(b.id)));
}
function interestScore(product, terms) {
 const title = (product.title || '').toLowerCase().replace(/[^\p{L}\d]+/gu, ' ').trim();
 const words = new Set(title.split(' '));
 let score = 0;
 for (const term of terms) {
   const parts = term.toLowerCase().split(/[^\p{L}\d]+/u).filter(Boolean);
   if (parts.length && parts.every(word => words.has(word))) score += parts.length;
 }
 return score;
}

function comparison(product, now = Date.now()) {
 const current = dealView(product, now);
 if (!current.fresh || !current.valid || !current.validated) return null;
 const offers = (product.evidence?.cross_store_offers || []).filter(o =>
   o.condition_confirmed && o.store !== product.store && ACTIVE_STORES.includes(o.store) &&
   ageHours(o.last_seen, now) <= 24 && Number.isFinite(o.price) && o.price > 0);
 if (!offers.length) return null;
 const withShipping = [product, ...offers].every(o => Number.isFinite(o.shipping_sar) && o.shipping_sar >= 0);
 const cost = o => o.price + (withShipping ? o.shipping_sar : 0);
 const peer = offers.sort((a,b) => cost(a) - cost(b))[0];
 return {peer, withShipping, difference: Math.abs(cost(peer)-cost(product)),
         cheaper: cost(peer) < cost(product) ? peer.store : product.store};
}

function gpuComparison(title) {
 if (!/graphics card|video card|بطاقة رسو|كرت شاشة/i.test(title) ||
     /laptop|notebook|desktop pc|gaming pc|waterblock|replacement|cooling fan|backplate/i.test(title)) return null;
 const models = [...title.toLowerCase().matchAll(/\brtx\s*[- ]?(\d{4})(?:\s*(ti))?(?:\s*(super))?\b/g)];
 if (models.length !== 1) return null;
 const model = models[0].slice(1).filter(Boolean).join(' ');
 const fps = {'3080 ti':103.9,'4070':97.8,'4070 super':109.8,'4070 ti':116.5,'4090':146.1,'4080':130.4,'3090 ti':112.7,'3090':106,'4080 super':133,'4070 ti super':122}[model];
 return fps ? Math.round(100*(fps / 103.9 - 1)) : null;
}

const fallbackTerms = ["gaming PC", "IEM earphones", "USB C multiport 100W charger", "fresh daily office fragrance men", "omega 3", "iPhone accessories", "gaming controller", "NVIDIA RTX graphics card", "TWS earbuds", "USB C cable", "Dior Sauvage eau de toilette", "vitamin D3", "iPad accessories", "Fortnite controller accessories", "Intel Core i5 processor", "ANC noise cancelling headphones", "Lightning cable", "clean aquatic fragrance men", "magnesium glycinate", "CPU GPU temperature display", "Call of Duty gaming headset", "Intel Core i7 processor", "over ear headphones", "USB adapter", "Versace Pour Homme", "multivitamins minerals", "USB hub", "gaming gear", "motherboard", "gaming headset", "UGREEN USB C charger", "fresh citrus fragrance men", "energy focus supplements", "PC accessories", "DDR5 RGB RAM", "NVMe SSD", "PC power supply PSU", "CPU cooler", "PC case", "QHD 240Hz IPS gaming monitor", "iHerb California Gold Nutrition", "LG UltraGear 27GR83Q", "RTX 3080 Ti", "FIFINE microphone", "PS5 DualSense accessories", "Philips electric shaver", "gaming mouse", "mechanical keyboard", "Baseus GaN charger", "microphone", "audio mixer", "gaming monitor 2K 240Hz", "4K television", "graphics card", "computer accessories", "SSD NVMe", "RAM DDR5", "USB C 100W GaN charger", "UK plug charger", "power bank", "car accessories", "dash cam", "car phone holder", "robot vacuum", "air fryer", "coffee machine", "men hoodie", "men sneakers", "men baseball cap", "men jacket", "men perfume", "fresh citrus men's fragrance", "men grooming trimmer", "travel gadgets", "home organization gadgets", "smart home devices", "fitness accessories", "desk accessories", "kitchen gadgets", "DIY tools", "outdoor accessories", "lighting gadgets", "wallet", "backpack", "sports shoes", "wireless charging stand", "ugreen", "baseus", "gan", "100w", "usb-c", "usb c", "anc", "noise cancelling", "2k", "240hz", "oled", "rtx", "ddr5", "fresh", "citrus", "aquatic", "clean", "hoodie", "sneaker"];
if(typeof module!=='undefined'&&module.exports){module.exports={interestScore,comparison,gpuComparison,dealView,selectProducts,ageHours};}else{initialize();}
function initialize(){
 const $=id=>document.getElementById(id);
 const ENDPOINT=['127.0.0.1','localhost'].includes(location.hostname)?'/api/dashboard':'https://raw.githubusercontent.com/kkoo98767-cell/KHALID-DEAL-HUNTER/main/data/dashboard.json';
 const paths={grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',shield:'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z M8 12l3 3 5-6',archive:'M4 7h16v14H4z M3 3h18v4H3z M9 11h6',heart:'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8z',info:'M12 10v7 M12 7h.01 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',refresh:'M20 7v5h-5 M4 17v-5h5 M6.1 6.1a8 8 0 0 1 13 2.4 M4.9 15.5a8 8 0 0 0 13 2.4',arrow:'M19 12H5 M11 6l-6 6 6 6',radar:'M12 12l8-8 M20.5 10a9 9 0 1 1-7-7 M16 12a4 4 0 1 1-4-4',tag:'M20 13l-7 7L3 10V3h7z M7 7h.01',chart:'M3 3v18h18 M6 15l4-5 4 3 6-8',search:'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',close:'M6 6l12 12 M6 18 18 6',box:'M3 7l9-4 9 4v11l-9 4-9-4z M3 7l9 4 9-4 M12 11v11 M7 5l10 4',clock:'M12 7v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',external:'M14 3h7v7 M21 3 11 13 M10 3H3v18h18v-7',chevron:'M14 6l-6 6 6 6'};
 function icon(name){const n=document.createElementNS('http://www.w3.org/2000/svg','svg');n.setAttribute('viewBox','0 0 24 24');n.setAttribute('fill','none');n.setAttribute('stroke','currentColor');n.setAttribute('stroke-width','1.6');n.setAttribute('stroke-linecap','round');n.setAttribute('stroke-linejoin','round');n.setAttribute('aria-hidden','true');const path=document.createElementNS(n.namespaceURI,'path');path.setAttribute('d',paths[name]||paths.box);n.append(path);return n}
 document.querySelectorAll('[data-icon]').forEach(n=>n.append(icon(n.dataset.icon)));
 const fmt=n=>Number.isFinite(n)?n.toLocaleString('ar-SA',{minimumFractionDigits:2,maximumFractionDigits:2}):'غير متاح';
 const number=n=>Number.isFinite(n)?n.toLocaleString('ar-SA'):'—';
 const percent=n=>Number.isFinite(n)?n.toLocaleString('ar-SA',{maximumFractionDigits:2})+'٪':'غير متاح';
 const date=s=>{const d=new Date(s?.includes('T')?s:s?.replace(' ','T')+'Z');return Number.isFinite(d.getTime())?d.toLocaleString('ar-SA',{timeZone:'Asia/Riyadh',dateStyle:'short',timeStyle:'short'}):'غير متاح'};
 const storeName=s=>s==='Jarir'?'جرير':'أمازون السعودية';
 let data,busy=false,visible=24,state={view:'offers',store:'',search:'',sort:'discount',includeReview:false},favorites=new Set();
 try{const saved=JSON.parse(localStorage.getItem('khalid-favorites')||'[]');if(Array.isArray(saved))favorites=new Set(saved.filter(v=>typeof v==='string'))}catch{}
 function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=text;if(cls)n.className=cls;return n}
 function link(text,url,cls){try{const u=new URL(url);const host=u.hostname;if(u.protocol!=='https:'||!['www.amazon.sa','amazon.sa','www.jarir.com','jarir.com'].includes(host))return el('span','الرابط غير متاح',cls);const a=el('a',text,cls);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';return a}catch{return el('span','الرابط غير متاح',cls)}}
 function imagePlaceholder(){const n=el('div',undefined,'image-unavailable');n.append(icon('box'),el('span','صورة المنتج غير متاحة'));return n}
 function safeImage(url){try{const u=new URL(url);return u.protocol==='https:'&&/^(?:[a-z0-9-]+\.)*(?:amazon\.sa|media-amazon\.com|ssl-images-amazon\.com|jarir\.com|jarircdn\.com)$/.test(u.hostname)?u.href:null}catch{return null}}
 function storeMark(store){return el('b',store==='Jarir'?'ج':'a',store==='Jarir'?'jarir-j':'amazon-a')}
 function badge(d){const n=el('span',undefined,'status-badge'+(d.verified?' verified':d.potential?'':' neutral'));n.append(icon(d.verified?'shield':'clock'),document.createTextNode(d.status));return n}
 function historyProgress(p){const h=p.history_summary||{};const days=h.observed_days||0,required=h.required_days||7,span=h.coverage_days||0,needed=h.required_coverage_days||7;return Math.min(100,days/required*100,span/needed*100)}
 function renderPersonal() {
   const shortlist = data.products.map(p => ({p, score: interestScore(p, data.interest_terms || fallbackTerms)}))
     .filter(({p,score}) => score && dealView(p).fresh && dealView(p).valid && dealView(p).validated)
     .sort((a,b) => Number(dealView(b.p).verified)-Number(dealView(a.p).verified) || b.score-a.score || b.p.price-a.p.price);
   const pick = shortlist.map(entry => entry.p).filter(p => dealView(p).verified)
     .sort((a,b) => dealView(b).percent-dealView(a).percent)[0];
   const spot = $('daily-pick');
   spot.replaceChildren(el('h1','لقطة اليوم'));
   if (pick) {
     const button = el('button','شوف سجل السعر','secondary-button');
     button.onclick = () => openDetails(pick);
     spot.append(el('h2',pick.title),el('p',storeName(pick.store)+' · '+fmt(pick.price)+' ريال · أقل من المعتاد بـ '+fmt(dealView(pick).amount)+' ريال'),button);
     if (pick.lowest_observed_30d === pick.price) spot.append(el('small','أقل سعر رصدناه لهذا المنتج خلال آخر ٣٠ يوم'));
   } else spot.append(el('p','اليوم ما رصدنا لقطة من اهتماماتك تستاهل بسعر يدعمه السجل.'));
   const panel = $('personal-picks');
   panel.replaceChildren();
   for (const {p} of shortlist.slice(0,3)) {
     const button = el('button',undefined,'personal-product');
     button.append(el('strong',p.title),el('span',storeName(p.store)+' · '+fmt(p.price)+' ر.س · '+dealView(p).status));
     button.onclick = () => openDetails(p);
     panel.append(button);
   }
   if (!panel.childElementCount) panel.append(el('p','ننتظر قراءة حديثة لمنتج من اهتماماتك.'));
 }
 function compareCard(p) {
   const match = comparison(p);
   if (!match) return null;
   const box=el('div',undefined,'match-box');
   box.append(el('strong','أمازون مقابل جرير'),link(storeName(match.peer.store)+' · '+fmt(match.peer.price)+' ريال',match.peer.url,'text-button'),
     el('span',match.difference ? storeName(match.cheaper)+' أوفر بـ '+fmt(match.difference)+' ريال' : 'السعر متساوٍ'),
     el('small',match.withShipping?'الفرق يشمل الشحن المرصود':'فرق سعر السلعة قبل الشحن'));
   return box;
 }
 function card(p){const d=dealView(p),c=el('article',undefined,'product-card'),visual=el('div',undefined,'product-visual');
 const imageURL=safeImage(p.image_url);if(imageURL){const img=el('img');img.src=imageURL;img.alt=p.title;img.loading='lazy';img.decoding='async';img.referrerPolicy='no-referrer';img.onerror=()=>img.replaceWith(imagePlaceholder());visual.append(img)}else visual.append(imagePlaceholder());
 if(d.percent!==null){const stamp=el('div',undefined,'discount-stamp'+(d.verified?' confirmed':''));stamp.append(el('strong','−'+percent(d.percent)),el('small',d.source));visual.append(stamp)}
 const fav=el('button',undefined,'favorite-button'+(favorites.has(p.id)?' saved':''));fav.append(icon('heart'));fav.setAttribute('aria-label','تبديل المفضلة: '+p.title);fav.setAttribute('aria-pressed',String(favorites.has(p.id)));fav.onclick=()=>{favorites.has(p.id)?favorites.delete(p.id):favorites.add(p.id);try{localStorage.setItem('khalid-favorites',JSON.stringify([...favorites]))}catch{}render()};visual.append(fav);c.append(visual);
 const body=el('div',undefined,'product-content'),meta=el('div',undefined,'product-meta'),store=el('span',undefined,'store-label');store.append(storeMark(p.store),document.createTextNode(storeName(p.store)));meta.append(store,badge(d));body.append(meta,el('h3',p.title));
 const prices=el('div',undefined,'price-row');prices.append(el('strong',fmt(p.price),'current-price'),el('span','ر.س','currency'));if(Number.isFinite(p.store_original_price)&&p.store_original_price>p.price){const original=el('span',undefined,'original-price');original.append(el('del',fmt(p.store_original_price)),el('small','سعر سابق معلن','original-label'));prices.append(original)}body.append(prices);
 body.append(el('div',d.amount!==null?(d.verified?'توفير تاريخي ':'توفير معلن ')+fmt(d.amount)+' ر.س':d.verified?'انخفاض مثبت بالسجل':'التوفير التاريخي غير مثبت بعد','saving-row'+(d.verified?' real':'')));
 const actual=p.evidence?.historical_savings;
 if(d.fresh && d.validated && d.valid && p.evidence?.historical_evidence_valid && Number.isFinite(actual?.amount_sar))
   body.append(el('p','التوفير عن المعتاد: '+fmt(actual.amount_sar)+' ريال ('+percent(actual.percent)+')','actual-saving'));
 else body.append(el('small',d.fresh?'نجمع سجل سعر موثوق':'بانتظار تحديث السعر'));
 const matchBox=compareCard(p); if(matchBox) body.append(matchBox);
 const baseline=el('div',undefined,'baseline-row');baseline.append(el('span','خط الأساس التاريخي'),el('b',Number.isFinite(p.baseline)?fmt(p.baseline)+' ر.س':'بانتظار سجل كافٍ'));body.append(baseline);
 const hs=p.history_summary||{},history=el('div',undefined,'history-row');history.append(icon('chart'),document.createTextNode(number(hs.observed_days||0)+' / '+number(hs.required_days||7)+' أيام مقارنة موثّقة'));body.append(history);const track=el('div',undefined,'maturity-track'),bar=el('span');bar.style.width=historyProgress(p)+'%';track.append(bar);track.setAttribute('aria-label','اكتمال أيام ومدة السجل: '+Math.round(historyProgress(p))+'٪');body.append(track);
 const trust=el('div',undefined,'trust-row');trust.append(el('span','قوة الأدلة · ليست جودة المنتج'),el('b',number(d.score)+' / 100'));body.append(trust);
 const actions=el('div',undefined,'card-actions'),details=el('button','كل التفاصيل','secondary-button');details.append(icon('chevron'));details.onclick=()=>openDetails(p);const outbound=link('افتح المتجر',p.url,'primary-button');outbound.append(icon('external'));actions.append(outbound,details);body.append(actions,el('div','آخر رصد: '+date(p.last_seen),'card-time'));c.append(body);return c;
 }
 function plot(points){const box=document.createElementNS('http://www.w3.org/2000/svg','svg');box.setAttribute('viewBox','0 0 640 130');box.setAttribute('class','detail-chart');box.setAttribute('role','img');box.setAttribute('aria-label','رسم آخر قراءات السعر المؤرشفة؛ بعضها قد يكون غير مدقق');const values=points.map(p=>p.price).filter(Number.isFinite);if(!values.length)return box;const min=Math.min(...values),max=Math.max(...values),line=document.createElementNS(box.namespaceURI,'polyline');line.setAttribute('points',values.map((v,i)=>`${values.length===1?320:20+i/(values.length-1)*600},${108-(v-min)/(max-min||1)*85}`).join(' '));line.setAttribute('fill','none');line.setAttribute('stroke','#315bec');line.setAttribute('stroke-width','2');box.append(line);for(const [i,v]of values.entries()){const dot=document.createElementNS(box.namespaceURI,'circle');dot.setAttribute('cx',values.length===1?320:20+i/(values.length-1)*600);dot.setAttribute('cy',108-(v-min)/(max-min||1)*85);dot.setAttribute('r','3');dot.setAttribute('fill','#315bec');box.append(dot)}return box}
 function openDetails(p){const d=dealView(p),e=p.evidence||{},h=p.history_summary||{},content=$('detail-content');content.replaceChildren();const title=el('h2',p.title,'detail-title');title.id='detail-title';content.append(title,badge(d));const priceGrid=el('div',undefined,'detail-prices');for(const [name,value]of [['السعر الحالي المرصود',p.price],['السعر الأصلي الذي يعلنه المتجر',p.store_original_price],['خط الأساس من قاعدة بياناتنا',p.baseline]]){const cell=el('div',undefined,'detail-price');cell.append(el('span',name),el('b',Number.isFinite(value)?fmt(value)+' ر.س':'غير متاح'));priceGrid.append(cell)}content.append(priceGrid);
 const evidence=el('section',undefined,'detail-section');evidence.append(el('h3','الخصم المعلن ليس هو الانخفاض الفعلي'));const ad=e.advertised_savings||{},hist=e.historical_savings||{};evidence.append(el('p',Number.isFinite(ad.percent)?'ادعاء المتجر: '+percent(ad.percent)+'، وتوفير معلن '+fmt(ad.amount_sar)+' ر.س.':'السعر الأصلي المعلن غير متاح؛ لا نحسب نسبة خصم من مرجع مفقود.'));evidence.append(el('p',d.verified?'ما يثبته سجلنا فعلياً: انخفاض '+percent(hist.percent)+' وتوفير '+fmt(hist.amount_sar)+' ر.س.':'لا يمكن اعتماد التوفير التاريخي لهذا العرض حالياً. نحتاج بيانات كافية وسعراً حديثاً قبل وصفه بالموثوق.'));evidence.append(el('p','قوة الأدلة '+number(d.score)+' من 100؛ ليست تقييماً للجودة أو ضماناً للشراء.'));content.append(evidence);
 const reasons=el('ul',undefined,'detail-warnings');for(const warning of e.warnings||[])reasons.append(el('li',warning));if(!d.fresh)reasons.append(el('li','هذه القراءة قديمة؛ تم إلغاء علامة الثقة في الواجهة حتى وصول سعر حديث.'));content.append(reasons);
 const chart=el('section',undefined,'detail-section');chart.append(el('h3','تاريخ السعر، كما رصدناه'),plot((p.history||[]).slice(-60)),el('p','القراءات الموثّقة: '+number(h.validated_observations||0)+'؛ أيام المقارنة السابقة: '+number(h.observed_days||0)+'؛ الامتداد: '+number(h.coverage_days||0)+' يوماً. نحتاج '+number(h.required_days||7)+' أيام رصد وامتداد '+number(h.required_coverage_days||7)+' أيام على الأقل. القراءات القديمة غير المدققة تُحفظ ولا تثبت الخصم.'));const table=el('table',undefined,'history-table'),head=el('thead'),headRow=el('tr');for(const label of ['وقت الرصد','السعر','نوع القراءة'])headRow.append(el('th',label));head.append(headRow);table.append(head);const tbody=el('tbody');for(const point of [...(p.history||[])].reverse().slice(0,20)){const row=el('tr');row.append(el('td',date(point.date)),el('td',fmt(point.price)+' ر.س'),el('td',point.validated?'موثّقة':'أرشيف غير مدقق'));tbody.append(row)}table.append(tbody);chart.append(table);content.append(chart);
 const opinion=el('section',undefined,'detail-section');opinion.append(el('h3','قراءة الذكاء الاصطناعي'),el('p',p.analysis?.telegram_message||'التقييم الآلي غير متاح لهذا المنتج. راجع المواصفات والضمان والمراجعات قبل اتخاذ قرار الشراء.'));content.append(opinion);
 const matchBox=compareCard(p); if(matchBox) content.append(matchBox);
 const gpu=gpuComparison(p.title);
 if(gpu!==null){
   const section=el('section',undefined,'detail-section'), source=el('a','اختبار Tom’s Hardware · 1440p Ultra');
   source.href='https://www.tomshardware.com/reviews/gpu-hierarchy,4388-2.html'; source.target='_blank';source.rel='noopener noreferrer';
   section.append(el('h3','مقارنة بكرتك RTX 3080 Ti'),el('p',gpu===0?'نفس مستوى كرتك في الاختبار':(gpu>0?'أسرع بنحو ':'أبطأ بنحو ')+number(Math.abs(gpu))+'٪ في متوسط الألعاب دون تتبع الأشعة'),source);content.append(section);
 }
 const peers=e.cross_store_offers||[];if(peers.length){const compare=el('section',undefined,'detail-section');compare.append(el('h3','أسعار لسلعة مطابقة بالمعرّف'));for(const other of peers)compare.append(link(storeName(other.store)+' · '+fmt(other.price)+' ر.س',other.url,'secondary-button'));compare.append(el('p','قد لا تشمل الأسعار الشحن أو الرسوم. مطابقة الحالة تحتاج تأكيداً إذا لم تتوفر بياناتها.'));content.append(compare)}
 const foot=el('div',undefined,'detail-foot');foot.append(link('شوف السعر في '+storeName(p.store),p.url,'primary-button'),el('span','آخر رصد: '+date(p.last_seen)));content.append(foot);$('details-dialog').showModal();
 }
 function setView(view){state.view=view;visible=24;document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===view);b.setAttribute('aria-current',b.dataset.view===view?'page':'false')});const titles={offers:['صفقات تستاهل المتابعة','خصومات معلنة 25٪+ وعروض ذات انخفاض تاريخي موثّق. حالة كل عرض واضحة.','صفقات اليوم'],verified:['عروض الثقة ما جات من فراغ','سعر أقل من خط أساس مبني على سجل كافٍ، مع قراءة حديثة وبيانات سليمة.','العروض الموثوقة'],all:['أرشيف اليوم… دليل بكرة','كل المنتجات الفريدة في أمازون وجرير. نحفظ السعر حتى لو ما عليه عرض.','كل المنتجات'],favorites:['عروضك، في مكان واحد','قائمة محفوظة على هذا المتصفح. نحدّث حالة العروض مع تغيّر الأسعار.','المفضلة']};const t=titles[view];$('view-title').replaceChildren(document.createTextNode(t[0]),el('span','.','heading-dot'));$('view-description').textContent=t[1];$('breadcrumb-current').textContent=t[2];$('review-filter').hidden=view!=='all';render()}
 function render(){if(!data)return;const products=selectProducts(data.products,{...state,favorites});$('count').textContent=number(products.length)+' '+(state.view==='offers'||state.view==='verified'?'عرض':'منتج')+' · أمازون وجرير';const fragment=document.createDocumentFragment();for(const p of products.slice(0,visible))fragment.append(card(p));if(!products.length){const empty=el('div',undefined,'empty-state');empty.append(icon(state.view==='verified'?'shield':state.view==='favorites'?'heart':'search'));const titles=state.search?'ما لقينا نتيجة بهذا البحث':state.view==='verified'?'الثقة تحتاج سجل… وما نستعجلها':state.view==='favorites'?'لسّه ما حفظت منتجاتك المفضلة':'نترقب صفقات مطابقة للشروط';empty.append(el('h3',titles),el('p',state.view==='verified'?'ما فيه عرض يستوفي شروط التحقق حالياً. لن نضع علامة موثوق على خصم معلن فقط.':'غيّر الفلاتر أو تصفّح الأرشيف. المنتجات تُضاف من قراءات فعلية؛ ما نعبّي الصفحة بعروض تجريبية.'));const all=el('button','تصفّح كل المنتجات');all.onclick=()=>{state.search='';$('search').value='';state.store='';document.querySelectorAll('[data-store]').forEach(b=>b.classList.toggle('selected',b.dataset.store===''));setView('all')};empty.append(all);fragment.append(empty)}$('products').replaceChildren(fragment);$('more').hidden=visible>=products.length;$('nav-favorites').textContent=number(data.products.filter(p=>favorites.has(p.id)).length);$('nav-offers').textContent=number(data.products.filter(p=>{const d=dealView(p);return d.featured||d.verified}).length);$('nav-verified').textContent=number(data.products.filter(p=>dealView(p).verified).length)}
 function updateStats(){renderPersonal();const active=data.products.filter(p=>ACTIVE_STORES.includes(p.store));$('stat-featured').textContent=number(active.filter(p=>dealView(p).featured).length);$('stat-verified').textContent=number(active.filter(p=>dealView(p).verified).length);$('stat-products').textContent=number(active.length);$('stat-observations').textContent=number(data.stats.observations);$('archive-time').textContent='الأرشيف: '+date(data.generated_at);const health=$('store-health');health.replaceChildren();for(const store of ACTIVE_STORES){const r=data.scan?.stores?.[store],item=el('div',undefined,'health-item');item.title=r?.error?'تعذر إكمال آخر فحص':r?.products?'نتائج البحث في آخر فحص':'لم تصل نتائج بحث صالحة في آخر فحص';const dot=el('i',undefined,'health-dot'+(r?.error||r?.products===0?' bad':r?.products?' good':''));item.append(dot,el('strong',storeName(store)),el('small',!r?'بانتظار الرصد':r.error?'تعذر الفحص':r.products?number(r.products)+' نتائج':'بلا نتائج'));health.append(item)}}
 let requestedProduct = new URLSearchParams(location.search).get('product');
 async function load(){if(busy)return;busy=true;$('refresh').disabled=true;try{const url=ENDPOINT.startsWith('https:')?ENDPOINT+'?v='+Math.floor(Date.now()/60000):ENDPOINT;const r=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error('HTTP '+r.status);const payload=await r.json();if(!Array.isArray(payload.products)||!payload.stats)throw Error('Invalid archive');data={...payload,products:payload.products.filter(p=>ACTIVE_STORES.includes(p.store))};$('connection').textContent='آخر مزامنة '+new Date().toLocaleTimeString('ar-SA',{timeZone:'Asia/Riyadh',hour:'2-digit',minute:'2-digit'});const stale=Date.now()-Date.parse(data.generated_at)>50400000;$('notice').classList.toggle('error',stale||Boolean(data.scan?.store_errors||data.scan?.empty_stores));$('notice').textContent=stale?'الأرشيف أقدم من ١٤ ساعة. الصفحة تتحدث تلقائياً، لكن لا نعتمد سعراً قديماً كعرض موثوق.':'نسبة المتجر باللون البرتقالي إعلان غير مؤكد. الأخضر لانخفاض تدعمه قراءاتنا التاريخية فقط.';const issues=ACTIVE_STORES.filter(s=>{const r=data.scan?.stores?.[s];return r&&(r.error||!r.products)});if(issues.length)$('notice').textContent+=' آخر فحص لم يكتمل بنتائج صالحة في '+issues.map(storeName).join(' و')+'.';updateStats();render();
 if(requestedProduct){const product=data.products.find(p=>p.id===requestedProduct || p.url===requestedProduct);if(product)openDetails(product);requestedProduct=null}
 }catch(error){$('connection').textContent='تعذر التحديث';$('notice').classList.add('error');$('notice').textContent=data?'تعذر تحميل تحديث جديد. المعروض آخر أرشيف متاح؛ راجع وقت رصد السعر قبل الاعتماد عليه.':'تعذر الاتصال بالأرشيف. لا نعرض خصومات أو أسعاراً تجريبية لملء الصفحة.';if(data){updateStats();render()}if(!data){$('daily-pick').replaceChildren(el('h1','لقطة اليوم'),el('p','تعذر تحميل الأسعار'));$('personal-picks').textContent='تعذر تحميل الأرشيف';$('products').replaceChildren();const empty=el('div',undefined,'empty-state');empty.append(icon('refresh'),el('h3','الأرشيف ما وصل بعد'),el('p','أعد المحاولة بعد قليل. بيانات الأسعار لن تظهر إلا بعد تحميلها من الأرشيف الفعلي.'));$('products').append(empty);$('count').textContent='البيانات غير متاحة مؤقتاً'}}finally{busy=false;$('refresh').disabled=false}}
 document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));document.querySelectorAll('[data-store]').forEach(b=>b.onclick=()=>{state.store=b.dataset.store;visible=24;document.querySelectorAll('[data-store]').forEach(n=>n.classList.toggle('selected',n===b));render()});$('search').addEventListener('input',()=>{state.search=$('search').value;visible=24;render()});$('sort').onchange=()=>{state.sort=$('sort').value;visible=24;render()};$('include-review').onchange=()=>{state.includeReview=$('include-review').checked;render()};$('more').onclick=()=>{visible+=24;render()};$('refresh').onclick=load;
 const showMethod=()=>$('method-dialog').showModal();$('open-method').onclick=showMethod;$('method-footer').onclick=showMethod;$('close-method').onclick=()=>$('method-dialog').close();$('close-details').onclick=()=>$('details-dialog').close();for(const id of ['method-dialog','details-dialog'])$(id).addEventListener('click',e=>{if(e.target===$(id)){const r=$(id).getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$(id).close()}});document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!$('details-dialog').open&&!$('method-dialog').open){e.preventDefault();$('search').focus()}});document.addEventListener('visibilitychange',()=>{if(!document.hidden)load()});setInterval(()=>{if(!document.hidden)load()},60000);for(let i=0;i<6;i++)$('products').append(el('div',undefined,'skeleton'));$('review-filter').hidden=true;load();
}
