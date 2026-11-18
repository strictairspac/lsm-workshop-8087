"""Standard-library-only checks for the dual-site sync engine.
Run: python3 -I test_artifacts/gb-4106/test_sync_engine.py
Imported via path so no package deps (fastapi/sqlalchemy) are needed.
"""
import importlib.util,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('sync_engine',ROOT/'backend/src/services/sync_engine.py')
eng=importlib.util.module_from_spec(spec);spec.loader.exec_module(eng)
fails=[]
def check(name,cond,detail=''):
    print(('PASS' if cond else 'FAIL'),name,detail)
    if not cond:fails.append(name)

# 1. Offline writes stay local; each site reads/writes its own kv.
s=eng.new_state()
eng.set_link(s,False)
eng.write_local(s,'A','put','k1','a1')
eng.write_local(s,'B','put','k1','b1')
eng.write_local(s,'B','put','k2','b2')
check('offline local divergence',s['sites']['A']['kv']=={'k1':'a1'} and s['sites']['B']['kv']=={'k1':'b1','k2':'b2'})
check('offline sync blocked',eng.sync_batch(s)['blocked'] is True)

# 2. Reconnect -> converge; LWW by Lamport clock (B's k1 clock 1 == A's clock 1 -> origin tie-break B).
eng.set_link(s,True);r=eng.drain(s)
v=eng.verify(s)
check('converge after reconnect',v['ok'] and v['converged'],str(v))
check('lww tie-break deterministic',s['sites']['A']['kv']==s['sites']['B']['kv']=={'k1':'b1','k2':'b2'},str(s['sites']['A']['kv']))
check('conflict recorded',any(c['key']=='k1' and c['kind']=='concurrent_put' for c in s['conflicts']))

# 3. Late old write must not resurrect a deleted key (tombstone has higher clock).
s=eng.new_state()
eng.write_local(s,'A','put','sensor-x','old-value')       # A-1
eng.set_link(s,False)
eng.write_local(s,'A','delete','sensor-x')                 # A-2 tombstone while partitioned
eng.write_local(s,'B','put','sensor-x','stale-overwrite')  # B-1 older clock
eng.set_link(s,True);eng.drain(s)
v=eng.verify(s)
check('no resurrection after late put',v['converged'] and 'sensor-x' not in s['sites']['A']['kv'] and 'sensor-x' not in s['sites']['B']['kv'],str(v))
check('tombstone conflict classified',any(c['kind']=='tombstone_vs_put' for c in s['conflicts']))

# 4. Mid-sync disconnect then resume: retry must not re-apply old ops.
s=eng.new_state();eng.set_link(s,False)
for i in range(6):eng.write_local(s,'A','put',f'ka{i}',str(i))
for i in range(4):eng.write_local(s,'B','put',f'kb{i}',str(i))
eng.set_link(s,True)
r1=eng.sync_batch(s,batch_limit=3)  # partial: 3 B->A then 3 A->B
applied_before=(len(s['sites']['A']['log']),len(s['sites']['B']['log']))
eng.set_link(s,False)
blocked=eng.sync_batch(s,batch_limit=3)
eng.set_link(s,True)
r2=eng.sync_batch(s,batch_limit=3)  # resume
dup_events=[e for e in s['events'] if e['event']=='op_apply']
ids_a=[eng.op_id(o) for o in s['sites']['A']['log']];ids_b=[eng.op_id(o) for o in s['sites']['B']['log']]
check('partial sync moved ops',r1['exchanged']==6 and r1['completed'] is False,str(r1))
check('sync while down blocked',blocked['blocked'] is True)
check('resume drains remaining',all(x['completed'] for x in eng.drain(s)) or eng.verify(s)['converged'])
check('no duplicate op ids',len(ids_a)==len(set(ids_a)) and len(ids_b)==len(set(ids_b)))
check('idempotent retries',eng.verify(s)['ok'],str(eng.verify(s)))

# 5. Backfill window: writes still accepted while catching up, then converge.
s=eng.new_state();eng.set_link(s,False)
for i in range(5):eng.write_local(s,'A','put','backlog',f'a{i}')
eng.set_link(s,True)
eng.sync_batch(s,batch_limit=2)
eng.write_local(s,'B','put','backlog','fresh-during-backfill')  # allowed mid-catch-up
eng.drain(s);v=eng.verify(s)
check('writes during backfill accepted',v['converged'],str(v))

# 6. Scenario from the brief: one site deletes, other overwrites concurrently.
for policy,expect_live in [('lww',None),('delete_wins',False),('put_wins',True)]:
    s=eng.new_state(policy=policy);eng.set_link(s,False)
    eng.write_local(s,'A','delete','conflict-key')          # A-1
    eng.write_local(s,'B','put','conflict-key','cover')     # B-1 equal clock
    eng.set_link(s,True);eng.drain(s);v=eng.verify(s)
    live='conflict-key' in s['sites']['A']['kv']
    if policy=='lww':ok=v['converged'] and live  # origin B > A
    elif policy=='delete_wins':ok=v['converged'] and not live
    else:ok=v['converged'] and live and s['sites']['A']['kv']['conflict-key']=='cover'
    check(f'policy {policy} delete-vs-put',ok,str(s['sites']['A']['kv']))

# 7. Repeated sync rounds on an already-converged state change nothing.
s=eng.new_state();eng.write_local(s,'A','put','z','1');eng.drain(s)
snap=[len(x['log']) for x in s['sites'].values()]
for _ in range(5):eng.sync_batch(s)
check('empty sync is a no-op',[len(x['log']) for x in s['sites'].values()]==snap and eng.verify(s)['converged'])

# 8. Validation errors.
s=eng.new_state()
for bad in [lambda:eng.write_local(s,'A','bad','k'),lambda:eng.write_local(s,'A','put',''),lambda:eng.write_local(s,'C','put','k','v')]:
    try:bad();check('validation rejects bad input',False)
    except ValueError:check('validation rejects bad input',True)

# 9. Determinism: same op schedule twice -> identical checksums.
def schedule():
    s=eng.new_state('delete_wins');eng.set_link(s,False)
    eng.write_local(s,'A','put','q','1');eng.write_local(s,'B','put','q','2')
    eng.write_local(s,'A','delete','q');eng.set_link(s,True);eng.sync_batch(s,batch_limit=1);eng.drain(s)
    return eng.verify(s)['checksum_a']
check('deterministic outcome',schedule()==schedule())

# 10. Seeded regression scenario: delete vs concurrent overwrite, then converge online.
s=eng.new_state('lww')
eng.write_local(s,'A','put','sensor-01','18.2');eng.write_local(s,'B','put','sensor-01','18.2')
eng.set_link(s,False)
eng.write_local(s,'A','put','sensor-01','19.0');eng.write_local(s,'A','delete','sensor-02')
eng.write_local(s,'B','put','sensor-02','复活探针-旧写');eng.write_local(s,'B','put','sensor-09','21.6')
eng.set_link(s,True);rounds=eng.drain(s);v=eng.verify(s)
check('seeded drill converges once online',v['converged'] and len(rounds)==1)
check('tombstone blocks resurrection in seed set',v['ok'] and 'sensor-02' not in s['sites']['A']['kv'] and s['sites']['A']['kv']==s['sites']['B']['kv']=={'sensor-01':'19.0','sensor-09':'21.6'},str(s['sites']['A']['kv']))

print()
if fails:print('FAILURES:',fails);sys.exit(1)
print('ALL SYNC ENGINE CHECKS PASSED')
