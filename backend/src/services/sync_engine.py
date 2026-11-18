"""Deterministic dual-site disconnected-write reconciliation engine.

Teaching model only: two logical sites keep their own key/value state while the
link is down. Every write is an immutable op stamped with a Lamport clock; a
higher clock always wins, so a tombstone that arrived late can never be undone by
an older put. Ties (true concurrency: equal clocks) are resolved by a fixed
policy and the station id tie-break, which makes both sites converge to the same
value regardless of the order in which batches arrive.

Each site keeps its full known op log (not just the winning op per key), so
backfill after a long partition replays every missing op and retries stay
idempotent. State lives entirely in the SyncDrill JSON column; this module has no
I/O and can be exercised with the standard library alone.
"""
import hashlib,json
STATIONS=('A','B')
POLICIES=('lww','delete_wins','put_wins')
MAX_OPS=200
KEY_LIMIT=64
VALUE_LIMIT=200

def new_state(policy='lww'):
    if policy not in POLICIES:raise ValueError('冲突策略必须是 lww / delete_wins / put_wins')
    site=lambda:{'online':True,'clock':0,'log':[],'versions':{},'kv':{}}
    return {'policy':policy,'sites':{'A':site(),'B':site()},'events':[],'conflicts':[]}

def checksum(kv):
    return hashlib.sha256(json.dumps(kv,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:16]

def op_id(op):return f"{op['origin']}-{op['clock']}"

def _rank(policy,op,current):
    """Return True when op must replace the current winning op for the same key."""
    if current is None:return True
    if op['clock']!=current['clock']:return op['clock']>current['clock']
    if op['deleted']!=current['deleted']:
        if policy=='delete_wins':return op['deleted']
        if policy=='put_wins':return not op['deleted']
    return op['origin']>current['origin']

def _log_event(state,event,payload):
    state['events'].append({'seq':len(state['events'])+1,'event':event,**payload})

def _known_ids(site):return {op_id(op) for op in site['log']}

def _apply(state,site_id,op,via,remote_site=None):
    """Idempotent: an op id already in the site log is never applied twice."""
    site=state['sites'][site_id];oid=op_id(op)
    if oid in _known_ids(site):return False
    key=op['key'];current=site['versions'].get(key);policy=state['policy']
    site['clock']=max(site['clock'],op['clock'])  # Lamport merge rule
    winner=_rank(policy,op,current)
    conflict=None
    # Any cross-origin write to a key the peer already changed is a reconciliation
    # decision the UI must show: equal clocks are truly concurrent, otherwise the
    # Lamport order decides and a newer tombstone survives an older late put.
    if current is not None and current['origin']!=op['origin'] and (current['deleted']!=op['deleted'] or op['clock']==current['clock']):
        kind='tombstone_vs_put' if op['deleted']!=current['deleted'] else 'concurrent_put'
        conflict={'key':key,'site':site_id,'kind':kind,'concurrent':op['clock']==current['clock'],'resolution':None,'kept':None,'dropped':None,'policy':policy,'clock':op['clock'],'losing_clock':current['clock']}
    if winner:
        site['versions'][key]=dict(op)
        if op['deleted']:site['kv'].pop(key,None)
        else:site['kv'][key]=op['value']
        if conflict:conflict.update(resolution='accepted',kept=oid,dropped=op_id(current))
    elif conflict:
        conflict.update(resolution='rejected',kept=op_id(current),dropped=oid)
    if conflict:state['conflicts'].append(conflict)
    site['log'].append(dict(op))
    _log_event(state,'op_apply',{'site':site_id,'via':via,'op':oid,'key':key,'deleted':op['deleted'],'changed':winner,'remote_site':remote_site})
    return True

def write_local(state,site_id,op_type,key,value=None):
    if site_id not in STATIONS:raise ValueError('站点必须是 A 或 B')
    if op_type not in ('put','delete'):raise ValueError('操作必须是 put 或 delete')
    if not isinstance(key,str) or not 1<=len(key)<=KEY_LIMIT:raise ValueError('键长度必须在1到64之间')
    if op_type=='put' and (not isinstance(value,str) or len(value)>VALUE_LIMIT):raise ValueError('put 的值必须是不超过200字的字符串')
    if len(state['sites']['A']['log'])+len(state['sites']['B']['log'])>=MAX_OPS:
        raise ValueError(f'单个演练最多保留{MAX_OPS}条操作，请重置后再试')
    site=state['sites'][site_id]
    site['clock']+=1
    op={'origin':site_id,'clock':site['clock'],'op':op_type,'key':key,'value':None if op_type=='delete' else value,'deleted':op_type=='delete'}
    _apply(state,site_id,op,'local')
    _log_event(state,'local_write',{'site':site_id,'op':op_id(op),'key':key,'deleted':op['deleted'],'online':site['online']})
    return op

def set_link(state,online):
    before={s:st['online'] for s,st in state['sites'].items()}
    for st in state['sites'].values():st['online']=bool(online)
    _log_event(state,'link',{'online':bool(online),'before':before})

def _ordered_log(site):
    return sorted(site['log'],key=lambda o:(o['clock'],o['origin']))

def backlog(state):
    a,b=state['sites']['A'],state['sites']['B']
    seen_a,seen_b=_known_ids(a),_known_ids(b)
    return sum(1 for op in b['log'] if op_id(op) not in seen_a)+sum(1 for op in a['log'] if op_id(op) not in seen_b)

def sync_batch(state,batch_limit=5):
    """One resumable, idempotent reconciliation round between A and B.

    Pull then push, each direction sending only ops the peer has not applied. A
    retry after an interrupted sync therefore never re-applies an old op.
    """
    if not isinstance(batch_limit,int) or not 1<=batch_limit<=MAX_OPS:raise ValueError('批量上限必须在1到200之间')
    a,b=state['sites']['A'],state['sites']['B']
    if not (a['online'] and b['online']):
        _log_event(state,'sync_blocked',{'reason':'link_down','a_online':a['online'],'b_online':b['online']})
        return {'exchanged':0,'applied_a':0,'applied_b':0,'remaining':backlog(state),'completed':False,'blocked':True,'batches':[]}
    report={'exchanged':0,'applied_a':0,'applied_b':0,'remaining':0,'completed':False,'blocked':False,'batches':[]}
    incoming=[op for op in _ordered_log(b) if op_id(op) not in _known_ids(a)][:batch_limit]  # B -> A
    for op in incoming:
        if _apply(state,'A',op,'sync',remote_site='B'):report['applied_a']+=1;report['exchanged']+=1
    if incoming:report['batches'].append({'direction':'B->A','size':len(incoming),'last_clock':incoming[-1]['clock']})
    outgoing=[op for op in _ordered_log(a) if op_id(op) not in _known_ids(b)][:batch_limit]  # A -> B
    for op in outgoing:
        if _apply(state,'B',op,'sync',remote_site='A'):report['applied_b']+=1;report['exchanged']+=1
    if outgoing:report['batches'].append({'direction':'A->B','size':len(outgoing),'last_clock':outgoing[-1]['clock']})
    report['remaining']=backlog(state);report['completed']=report['remaining']==0
    _log_event(state,'sync_batch',{'exchanged':report['exchanged'],'applied_a':report['applied_a'],'applied_b':report['applied_b'],'remaining':report['remaining'],'completed':report['completed']})
    return report

def drain(state,max_rounds=500):
    """Run batches until both sites hold every op (or the link is down)."""
    rounds=[]
    for _ in range(max_rounds):
        r=sync_batch(state);rounds.append(r)
        if r['blocked'] or r['completed']:return rounds
    raise RuntimeError('同步未能在限定轮次内收敛')

def verify(state):
    """Invariant checks. The exposed kv must be rebuildable from each site's op
    log (tombstones retained), no op id may appear twice, and once the backlog is
    empty both sites must agree — a late old put can never resurrect a key."""
    problems=[]
    for sid,site in state['sites'].items():
        rebuilt={}
        for op in _ordered_log(site):
            if op['deleted']:rebuilt.pop(op['key'],None)
            else:rebuilt[op['key']]=op['value']
        if rebuilt!=site['kv']:problems.append(f'{sid}: 键值与操作历史不一致')
        if len(site['log'])!=len(_known_ids(site)):problems.append(f'{sid}: 操作被重复应用')
        if checksum(rebuilt)!=checksum(site['kv']):problems.append(f'{sid}: 校验和不一致')
    remaining=backlog(state)
    if remaining==0 and state['sites']['A']['kv']!=state['sites']['B']['kv']:problems.append('两站操作已对齐但键值未收敛')
    return {'ok':not problems,'problems':problems,'backlog':remaining,
            'checksum_a':checksum(state['sites']['A']['kv']),'checksum_b':checksum(state['sites']['B']['kv']),
            'converged':remaining==0 and state['sites']['A']['kv']==state['sites']['B']['kv']}
