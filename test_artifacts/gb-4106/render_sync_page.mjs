// DOM-stub smoke render for the dual-site sync page (no browser needed).
const store={};
store['lsm-session']=JSON.stringify({email:'operator@beiling.test',role:'operator',token:'t'});
globalThis.localStorage={getItem:k=>store[k]??null,setItem:(k,v)=>{store[k]=v},removeItem:k=>delete store[k]};
globalThis.location={hash:'#sync/1'};
function makeEl(){return {innerHTML:'',textContent:'',classList:{add(){},remove(){}},dataset:{},querySelector(){return makeEl()},querySelectorAll(){return []},addEventListener(){},onclick:null,disabled:false,value:'3'}}
globalThis.document={querySelector:()=>makeEl(),createElement:()=>makeEl()};
globalThis.window={addEventListener(){},notificationTimer:null};
const payload={id:1,name:'测试演练',description:'',policy:'lww',created_at:'2026-11-18',online:false,backlog:3,converged:false,
  invariants:{ok:true,problems:[],backlog:3,checksum_a:'aaa',checksum_b:'bbb',converged:false},
  conflicts:[{key:'k',site:'A',kind:'tombstone_vs_put',concurrent:true,resolution:'accepted',kept:'A-1',dropped:'B-1',policy:'lww',clock:1,losing_clock:1}],
  events:[{seq:1,event:'link',site:null,online:false},{seq:2,event:'local_write',site:'A',key:'k1',deleted:false,online:false}],
  sites:{A:{online:false,clock:2,kv:{k1:'a'},op_count:2,tombstones:['k2'],checksum:'aaa'},B:{online:false,clock:1,kv:{k1:'b',k3:'c'},op_count:1,tombstones:[],checksum:'bbb'}}};
globalThis.fetch=async()=>({ok:true,json:async()=>payload});
const {syncPage}=await import('/workspace/frontend/src/pages/sync.js');
const el=makeEl();
await syncPage(el);
const html=el.innerHTML;
const need=['站点 A','站点 B','分歧','墓碑 vs 覆盖','剧本一','剧本二','Lamport','分批同步'];
for(const n of need){if(!html.includes(n))throw new Error('missing rendered fragment: '+n)}
console.log('DETAIL PAGE RENDER OK, html length',html.length);
// list view
globalThis.location={hash:'#sync'};
const listPayload=[{id:1,name:'演练一',description:'d',online:false,converged:false,backlog:3,conflicts:[1,2],sites:{A:{op_count:2},B:{op_count:1}}}];
globalThis.fetch=async()=>({ok:true,json:async()=>listPayload});
const el2=makeEl();
const mod=await import('/workspace/frontend/src/pages/sync.js?'+Date.now());
await mod.syncPage(el2);
if(!el2.innerHTML.includes('演练一')||!el2.innerHTML.includes('新建同步演练'))throw new Error('list view render failed');
console.log('LIST PAGE RENDER OK, html length',el2.innerHTML.length);
