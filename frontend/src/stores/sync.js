import{list,detail}from'../api/sync.js';
export const drills={
  items:[],current:null,
  async load(){this.items=await list();return this.items},
  async open(id){this.current=await detail(id);return this.current},
  clear(){this.current=null}
};
