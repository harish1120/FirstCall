import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.secrets import get_db_password

ENV = os.getenv("APP_ENV", "development")

if ENV == "production":
    secret_arn = os.getenv("RDS_SECRET_ARN")
    password = get_db_password(secret_arn)
    host = os.getenv("RDS_HOST")
    user = os.getenv("RDS_USER", "postgres")
    db = os.getenv("RDS_DB", "postgres")
    DATABASE_URL = f"postgresql+psycopg2://{user}:{password}@{host}:5432/{db}"
    connect_args = {}
    engine = create_engine(DATABASE_URL, echo=True, connect_args=connect_args)
else:
    DATABASE_URL = "sqlite:///./firstcall.db"
    connect_args = {"check_same_thread": False}
    engine = create_engine(DATABASE_URL, connect_args={
        "check_same_thread": False}, echo=True)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
