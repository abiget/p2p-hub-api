from typing import List

from sqlmodel import JSON, Column, Field, Relationship, SQLModel


class UserCreate(SQLModel):
    telegram_id: int = Field(index=True, unique=True)
    photo_url: str | None = None
    last_seen: str | None = None


class User(UserCreate, table=True):
    __tablename__ = "users"
    id: int | None = Field(default=None, primary_key=True)
    nick_name: str
    binance_user_id: str | None = Field(index=True, unique=True)
    created_at: str | None = None
    updated_at: str | None = None

    listings: List["Listing"] = Relationship(back_populates="user")


class ListingBase(SQLModel):
    advert_id: str = Field(index=True, unique=True)
    user_id: int = Field(foreign_key="users.id")
    binance_ad_url: str
    trade_type: str
    currency: str
    fiat_currency: str = Field(default="ETB")
    price: float
    min_limit: float
    max_limit: float
    tradable_quantity: float
    payment_methods: List[str] = Field(sa_column=Column(JSON))
    payment_time_limit: int | None = None
    active: bool = True
    created_at: str | None = None


class Listing(ListingBase, table=True):
    __tablename__ = "listings"
    id: int | None = Field(default=None, primary_key=True)

    updated_at: str | None = None

    user: "User" = Relationship(back_populates="listings")


class CreateListing(SQLModel):
    user: UserCreate
    binance_ad_url: str = Field(
        ..., regex=r"^https://c2c\.binance\.com/en/adv\?code=[^&]+$"
    )


class UpdateListing(SQLModel):
    binance_ad_url: str | None = Field(
        None, regex=r"^https://c2c\.binance\.com/en/adv\?code=[^&]+$"
    )


class UserRead(SQLModel):
    id: int
    telegram_id: int
    nick_name: str
    photo_url: str | None


class ListingRead(ListingBase):

    user: UserRead


class ListingsResponse(SQLModel):
    listings: list[ListingRead]
