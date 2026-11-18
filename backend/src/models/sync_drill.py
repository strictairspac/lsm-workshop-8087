from datetime import datetime
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,JSON,DateTime,func
from ..config.database import Base
class SyncDrill(Base):
    __tablename__='sync_drills'
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(120),unique=True)
    description:Mapped[str]=mapped_column(String(2000))
    policy:Mapped[str]=mapped_column(String(20))
    state:Mapped[dict]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
