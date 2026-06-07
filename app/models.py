from sqlalchemy import BigInteger, Column, Integer, String

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_nick = Column(String, unique=True, nullable=False, index=True)
    chat_id = Column(BigInteger, unique=True, nullable=False)
    role = Column(String, nullable=False, default="manager")
    department = Column(String, nullable=True)
