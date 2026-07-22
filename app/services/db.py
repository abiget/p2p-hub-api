from datetime import datetime
from sqlmodel import Session, SQLModel, select

from app.model import Listing, User, UserCreate


def create_db_and_tables(engine):
    SQLModel.metadata.create_all(engine)


def get_session(engine):
    def dependency():
        with Session(engine) as session:
            yield session

    return dependency


def check_user_exists(telegram_id: int, session: Session) -> User | None:
    statement = select(User).where(User.telegram_id == telegram_id)
    return session.exec(statement).first()


def check_listing_exists(advert_id: str, session: Session) -> Listing | None:
    statement = select(Listing).where(Listing.advert_id == advert_id)
    return session.exec(statement).first()


def get_or_create_user(
    user_data: UserCreate, advertiser_data: dict, session: Session
) -> User:
    user = check_user_exists(user_data.telegram_id, session)
    if not user:
        user = User(
            nick_name=advertiser_data["nickName"],
            binance_user_id=advertiser_data["userNo"],
            telegram_id=user_data.telegram_id,
            photo_url=user_data.photo_url,
            last_seen=user_data.last_seen,
            created_at=datetime.now().isoformat(),
        )
        session.add(user)
        session.flush()

    return user
