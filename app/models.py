from sqlalchemy import Column, Enum, Integer, String
from sqlalchemy.sql.expression import text
from sqlalchemy.sql.sqltypes import TIMESTAMP

from app.database import Base
from app.triage import Severity


class CallLog(Base):
    __tablename__ = 'calllog'

    id = Column(Integer, primary_key=True, index=True)
    call_sid = Column(String, unique=True, index=True)
    severity = Column(Enum(Severity), nullable=False)
    condition = Column(String, nullable=False)
    duration_seconds = Column(Integer, nullable=False, server_default=text('0'))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('CURRENT_TIMESTAMP'))

