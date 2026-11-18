import{api,post}from'./client.js';
export const list=()=>api('/sync-drills');
export const detail=id=>api('/sync-drills/'+id);
export const create=data=>post('/sync-drills',data);
export const reset=id=>post(`/sync-drills/${id}/reset`);
export const writeOp=(id,data)=>post(`/sync-drills/${id}/writes`,data);
export const setLink=(id,online)=>post(`/sync-drills/${id}/link`,{online});
export const runSync=(id,batch_limit=5)=>post(`/sync-drills/${id}/sync`,{batch_limit});
