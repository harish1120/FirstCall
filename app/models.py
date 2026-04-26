from app.database import Base
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Enum, null
from sqlalchemy.sql.sqltypes import TIMESTAMP
from sqlalchemy.sql.expression import text, func
from app.triage import Severity


class CallLog(Base):
    __tablename__ = 'calllog'

    id = Column(Integer, primary_key=True, index=True)
    call_sid = Column(String, unique=True, index=True)
    severity = Column(Enum(Severity), nullable=False)
    condition = Column(String, nullable=False)
    duration_seconds = Column(Integer, nullable=False, server_default=text('0'))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('CURRENT_TIMESTAMP'))

