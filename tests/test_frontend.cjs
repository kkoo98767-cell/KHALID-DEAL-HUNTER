const test=require('node:test');
const assert=require('node:assert/strict');
const {dealView,selectProducts,ageHours}=require('../website/app.js');
const now=Date.parse('2026-11-16T12:00:00Z');
function product(overrides={}){return {id:'one',store:'Amazon.sa',title:'Wireless Mouse',price:180,last_seen:'2026-11-16 11:00:00',status:'VERIFIED_DEAL',deal_type:'VERIFIED',history_summary:{latest_validated:true},evidence:{verified_deal:true,historical_evidence_valid:true,trust_score:80,historical_savings:{amount_sar:20,percent:10},advertised_savings:{amount_sar:120,percent:40}},...overrides};}
test('verified display uses actual10%, not advertised40%',()=>{const d=dealView(product(),now);assert.equal(d.verified,true);assert.equal(d.percent,10);assert.equal(d.amount,20);assert.equal(d.source,'انخفاض تاريخي');});
test('unvalidated latest record cannot become a featured or verified deal',()=>{const d=dealView(product({history_summary:{latest_validated:false}}),now);assert.equal(d.verified,false);assert.equal(d.featured,false);assert.equal(d.potential,false);});
test('stale data revokes green badge independently of cached backend flag',()=>{const d=dealView(product({last_seen:'2026-11-14 11:00:00'}),now);assert.equal(d.verified,false);assert.equal(d.potential,false);assert.equal(d.featured,false);assert.ok(d.score<=39);});
test('zero real savings can never be verified',()=>{const p=product();p.evidence.historical_savings={amount_sar:0,percent:0};assert.equal(dealView(p,now).verified,false);});
test('advertised25% is a potential deal with no historical proof',()=>{const p=product({status:'INSUFFICIENT_HISTORY'});p.evidence.verified_deal=false;p.evidence.historical_evidence_valid=false;p.evidence.advertised_savings={percent:25,amount_sar:60};const d=dealView(p,now);assert.equal(d.verified,false);assert.equal(d.potential,true);assert.equal(d.percent,25);});
test('retired stores never reappear even with an old cached feed',()=>{const p=product();assert.deepEqual(selectProducts([p,product({id:'two',store:'Noon'}),product({id:'three',store:'Extra'})],{view:'all',now}).map(p=>p.id),['one']);});
test('default offers hide ordinary monitoring records',()=>{const p=product({id:'normal',status:'PRICE_NORMAL'});p.evidence={verified_deal:false,historical_evidence_valid:false,historical_savings:{},advertised_savings:{}};assert.equal(selectProducts([p],{now}).length,0);assert.equal(selectProducts([p],{view:'all',now}).length,1);});
test('search, store and local favorites filters compose',()=>{const a=product(),b=product({id:'two',store:'Jarir',title:'Keyboard'});assert.equal(selectProducts([a,b],{view:'favorites',favorites:new Set(['two']),store:'Jarir',search:'key',now}).length,1);});
test('future or invalid timestamps never qualify as fresh',()=>{assert.equal(ageHours('2099-01-01',now),Infinity);assert.equal(ageHours('invalid',now),Infinity);});
test('offer comparison includes free delivery and rejects stale peers',()=>{
 const {comparison}=require('../website/app.js');
 const p=product({price:100,shipping_sar:0});
 const peer={store:'Jarir',price:95,shipping_sar:10,condition_confirmed:true,last_seen:p.last_seen};
 p.evidence.cross_store_offers=[peer];
 assert.equal(comparison(p,now).cheaper,p.store);
 assert.equal(comparison(p,now).difference,5);
 delete peer.shipping_sar;
 assert.equal(comparison(p,now).withShipping,false);
 peer.last_seen='2020-01-01';
 assert.equal(comparison(p,now),null);
});
test('personal matches use whole words and GPU comparisons require a desktop card',()=>{
 const {interestScore,gpuComparison}=require('../website/app.js');
 assert.equal(interestScore({title:'organizer'},['gan']),0);
 assert.ok(interestScore({title:'Samsung NVMe SSD'},['SSD NVMe']));
 assert.ok(gpuComparison('GeForce RTX 4080 Super graphics card')>0);
 assert.equal(gpuComparison('RTX 4080 Laptop'),null);
});
