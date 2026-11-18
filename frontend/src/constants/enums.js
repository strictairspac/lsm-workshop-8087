export const ROLES={admin:'管理员',operator:'操作员',viewer:'观察员'};export const STATUSES={queued:'排队中',running:'合并中',passed:'Oracle 一致',failed:'执行失败'};export const OPERATIONS={put:'写入',delete:'墓碑删除'};
export const SYNC_POLICIES={lww:'Lamport 最后写入胜（同钟按站点号）',delete_wins:'并发时删除胜',put_wins:'并发时写入胜'};
export const SYNC_EVENT={local_write:'本地写入',op_apply:'应用操作',sync_batch:'分批同步',sync_blocked:'同步被阻止',link:'链路切换'};
export const CONFLICT_KIND={tombstone_vs_put:'墓碑 vs 覆盖',concurrent_put:'并发覆盖'};
