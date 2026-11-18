from sqlalchemy import select
from src.config.database import Base,engine,Session
from src.models.user import User
from src.models.dataset import Dataset
from src.models.config import Config
from src.models.trial import Trial
from src.models.audit import Audit
from src.models.sync_drill import SyncDrill
from src.services import sync_engine as eng
from src.utils.password import hash_password
DEMO=[{'op':'put','key':'sensor-01','value':'18.2'},{'op':'put','key':'sensor-02','value':'19.1'},{'op':'put','key':'sensor-03','value':'22.4'},{'op':'put','key':'sensor-04','value':'17.0'},{'op':'put','key':'sensor-01','value':'18.8'},{'op':'delete','key':'sensor-02'},{'op':'put','key':'sensor-05','value':'21.6'},{'op':'put','key':'sensor-06','value':'20.3'},{'op':'delete','key':'sensor-03'},{'op':'put','key':'sensor-07','value':'23.0'},{'op':'put','key':'sensor-01','value':'19.0'},{'op':'put','key':'sensor-08','value':'18.9'}]
def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin','operator','viewer'):
            email=role+'@beiling.test'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,role=role,password_hash=hash_password('EdgeStore!17')))
        sets=[('北岭站点-早班写入','sensor-01重复写，sensor-02与sensor-03墓碑删除；12条操作，最终6个存活键。',DEMO),('旧值复活回归集','先将旧值刷入深层，再写墓碑；最终合并必须防止旧值复活。',[{'op':'put','key':'sensor-a','value':'old'},{'op':'put','key':'sensor-b','value':'b'},{'op':'put','key':'sensor-c','value':'c'},{'op':'put','key':'sensor-d','value':'d'},{'op':'delete','key':'sensor-a'},{'op':'put','key':'sensor-e','value':'e'}]),('全部删除边界集','两个已写入键随后全部删除，最终应是空字典。',[{'op':'put','key':'x','value':'1'},{'op':'put','key':'y','value':'2'},{'op':'delete','key':'x'},{'op':'delete','key':'y'}])]
        for name,desc,entries in sets:
            if not db.scalar(select(Dataset).where(Dataset.name==name)):db.add(Dataset(name=name,description=desc,entries=entries))
        configs=[('小内存-完整合并',2,2,3,True),('保留层级观测',2,3,3,False),('宽缓冲-完整合并',4,2,4,True)]
        for name,limit,trigger,depth,final in configs:
            if not db.scalar(select(Config).where(Config.name==name)):db.add(Config(name=name,memtable_limit=limit,l0_trigger=trigger,max_levels=depth,final_compaction=final))
        if not db.scalar(select(SyncDrill).where(SyncDrill.name=='双站断网-自由演练')):
            db.add(SyncDrill(name='双站断网-自由演练',description='空白双站：先断链各自写入，再重连分批同步，观察 Lamport 时钟与墓碑裁决。',policy='lww',state=eng.new_state('lww')))
        if not db.scalar(select(SyncDrill).where(SyncDrill.name=='双站断网-删键对峙回归集')):
            st=eng.new_state('lww')
            eng.write_local(st,'A','put','sensor-01','18.2');eng.write_local(st,'B','put','sensor-01','18.2')
            eng.set_link(st,False)
            eng.write_local(st,'A','put','sensor-01','19.0');eng.write_local(st,'A','delete','sensor-02')
            eng.write_local(st,'B','put','sensor-02','复活探针-旧写');eng.write_local(st,'B','put','sensor-09','21.6')
            eng.set_link(st,True)  # hand back with the link restored: the user runs the batches and watches convergence
            db.add(SyncDrill(name='双站断网-删键对峙回归集',description='断链期间 A 删除 sensor-02、B 同时覆盖旧值；重连后墓碑必须阻止旧值复活，两站最终一致。',policy='lww',state=st))
        db.commit()
        return {name:db.query(model).count() for name,model in [('users',User),('datasets',Dataset),('configs',Config),('trials',Trial),('sync_drills',SyncDrill)]}
if __name__=='__main__':print(seed())
