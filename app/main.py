import logging
import os
from datetime import datetime
from typing import Annotated, List
from urllib.parse import parse_qs, urlparse

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import NullPool
from sqlalchemy.exc import SQLAlchemyError

# from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import selectinload
from sqlmodel import Session, create_engine, select

from .model import CreateListing, Listing, ListingsResponse, UpdateListing, User
from app.services.api import get_binance_ad_data
from app.services.db import (
    check_listing_exists,
    create_db_and_tables,
    get_or_create_user,
    get_session,
)

logger = logging.getLogger(__name__)

load_dotenv()

RAW_DATABASE_URL = os.environ.get("DATABASE_URL")

if RAW_DATABASE_URL:
    if "://" in RAW_DATABASE_URL:
        _, connection_path = RAW_DATABASE_URL.split("://", 1)
    else:
        connection_path = RAW_DATABASE_URL

    DATABASE_URL = f"postgresql+psycopg://{connection_path}"

    engine = create_engine(DATABASE_URL, poolclass=NullPool)
else:
    # Local fallback for development on your machine
    engine = create_engine("sqlite:///database.db")


BINANCE_API_URL = os.environ.get("BINANCE_API_URL", "https://api.binance.com")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.http_client = httpx.Client()
    create_db_and_tables(engine)

    yield

    # Shutdown
    app.state.http_client.close()


def get_http_client(request: Request) -> httpx.Client:
    return request.app.state.http_client


SessionDep = Annotated[Session, Depends(get_session(engine))]
ClientDep = Annotated[httpx.Client, Depends(get_http_client)]

app = FastAPI(title="P2P Hub API", version="1.0.0", lifespan=lifespan)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def extract_binance_ad_code(binance_ad_url: str) -> str:
    parsed_url = urlparse(binance_ad_url)
    query_params = parse_qs(parsed_url.query)

    code_list = query_params.get("code")
    if not code_list:
        raise HTTPException(
            status_code=400,
            detail="Invalid Binance ad URL. 'code' parameter is missing.",
        )

    raw_code = code_list[0]
    binance_ad_code = raw_code.split(",")[0]

    return binance_ad_code


def get_clean_binance_ad_url(ad_code: str, binance_api_url: str) -> str:
    clean_url = f"{binance_api_url}?code={ad_code}"
    return clean_url


def populate_listing_with_ad_data(listing: Listing, ad_data: dict):
    listing.advert_id = ad_data["advNo"]
    listing.trade_type = ad_data["tradeType"]
    listing.currency = ad_data["asset"]
    listing.fiat_currency = ad_data["fiatUnit"]
    listing.price = float(ad_data["price"])
    listing.min_limit = float(ad_data["minSingleTransAmount"])
    listing.max_limit = float(ad_data["maxSingleTransAmount"])
    listing.tradable_quantity = float(ad_data["tradableQuantity"])
    listing.payment_methods = [
        method["identifier"] for method in ad_data["tradeMethods"]
    ]
    listing.payment_time_limit = ad_data.get("payTimeLimit")
    listing.active = ad_data["advStatus"] == "1"


def build_listing(
    ad_data: dict,
    user: User,
    clean_binance_ad_url: str,
) -> Listing:

    listing = Listing(
        user_id=user.id,
        binance_ad_url=clean_binance_ad_url,
        created_at=datetime.now().isoformat(),
    )

    populate_listing_with_ad_data(listing, ad_data)

    return listing


@app.post("/api/listings")
def create_listing(data: CreateListing, session: SessionDep, client: ClientDep):
    # get the listing form binace and get the price, min_limit, max_limit, trade_type, currency
    binance_ad_code = extract_binance_ad_code(data.binance_ad_url)
    clean_binance_ad_url = get_clean_binance_ad_url(binance_ad_code, BINANCE_API_URL)

    response = get_binance_ad_data(client, binance_ad_code, BINANCE_API_URL)

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Failed to fetch data from Binance API. Status code: {response.status_code}, Response: {response.text}",
        )
    binance_data = response.json().get("data", {})
    ad_data = binance_data.get("adv", {})
    advertiser_data = binance_data.get("advertiser", {})

    if not ad_data:
        raise HTTPException(
            status_code=404,
            detail=f"No advertisement data found for Binance ad code {binance_ad_code}.",
        )

    if not advertiser_data:
        raise HTTPException(
            status_code=404,
            detail=f"No advertiser data found for Binance ad code {binance_ad_code}.",
        )

    # check if user already exists
    user = get_or_create_user(data.user, advertiser_data, session)

    listing = check_listing_exists(ad_data["advNo"], session)

    if listing:
        raise HTTPException(
            status_code=400,
            detail=f"Listing with advert code {binance_ad_code} already exists.",
        )

    listing = build_listing(ad_data, user, clean_binance_ad_url)

    try:
        session.add(listing)
        session.commit()
        session.refresh(user)
        session.refresh(listing)
    except SQLAlchemyError as e:
        session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while creating the listing: {str(e)}",
        )

    logger.info(
        "New listing created advert_id=%s user_id=%s binance_ad_url=%s",
        listing.advert_id,
        user.id,
        listing.binance_ad_url,
    )

    return {"user": user, "listing": listing}


@app.put("/api/listings/{advert_id}")
def update_listing(
    advert_id: str, updated_data: UpdateListing, session: SessionDep, client: ClientDep
):
    listing = check_listing_exists(advert_id, session)

    if not listing:
        raise HTTPException(
            status_code=404, detail="Listing not found Please create it first."
        )

    data = updated_data.model_dump(exclude_unset=True)
    new_url = data.get("binance_ad_url", listing.binance_ad_url)
    binance_ad_code = extract_binance_ad_code(new_url)
    clean_ad_url = get_clean_binance_ad_url(binance_ad_code)
    response = get_binance_ad_data(client, binance_ad_code, BINANCE_API_URL)

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Failed to fetch data from Binance API. Status code: {response.status_code}, Response: {response.text}",
        )
    data = response.json().get("data", {})
    ad_data = data.get("adv", {})
    advertiser_data = data.get("advertiser", {})

    if not ad_data:
        raise HTTPException(
            status_code=404,
            detail=f"No advertisement data found for Binance ad code {binance_ad_code}.",
        )

    if not advertiser_data:
        raise HTTPException(
            status_code=404,
            detail=f"No advertiser data found for Binance ad code {binance_ad_code}.",
        )

    listing.binance_ad_url = clean_ad_url
    populate_listing_with_ad_data(listing, ad_data)
    listing.updated_at = datetime.now().isoformat()

    try:
        session.add(listing)
        session.commit()
        session.refresh(listing)
    except SQLAlchemyError as e:
        session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while updating the listing: {str(e)}",
        )

    logger.info(
        "Listing updated advert_id=%s user_id=%s",
        listing.advert_id,
        listing.user_id,
    )

    return {"listing": listing}


@app.delete("/api/listings/{advert_id}")
async def delete_listing(advert_id: str, session: SessionDep):
    listing = check_listing_exists(advert_id, session)

    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    try:
        session.delete(listing)
        session.commit()
    except SQLAlchemyError as e:
        session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while deleting the listing: {str(e)}",
        )

    logger.info("Listing deleted advert_id=%s user_id=%s", advert_id, listing.user_id)

    return {"detail": "Listing deleted successfully"}


@app.get("/api/my-listings/{telegram_id}", response_model=ListingsResponse)
async def get_my_listings(telegram_id: int, session: SessionDep):
    statement = (
        select(Listing)
        .join(User)
        .where((User.telegram_id == telegram_id) & (Listing.active == False))
        .options(selectinload(Listing.user))
        .order_by(Listing.created_at.desc())
    )

    listings = session.exec(statement).all()

    if not listings:
        raise HTTPException(status_code=404, detail="No listings found for this user")

    logger.info(
        "Retrieved %d listings for user with telegram_id=%d",
        len(listings),
        telegram_id,
    )

    return {"listings": listings}


@app.get("/api/listings", response_model=ListingsResponse)
def get_all_listings(
    session: SessionDep,
    side: str | None = None,
    nick_name: str | None = None,
    price_max: float | None = None,
    payment_methods: List[str] | None = Query(default=None),
    sort: str = "price_desc",
):
    statement = (
        select(Listing)
        .where(Listing.active == False)
        .options(selectinload(Listing.user))
    )

    if side:
        statement = statement.where(Listing.trade_type == side.upper())

    if price_max is not None:
        statement = statement.where(Listing.max_limit >= price_max)

    if payment_methods is not None:
        statement = statement.where(Listing.payment_methods.contains(payment_methods))

    if nick_name is not None:
        statement = statement.where(Listing.nick_name, "LIKE", nick_name)

    if sort == "price_asc":
        statement = statement.order_by(Listing.price.asc())
    elif sort == "price_desc":
        statement = statement.order_by(Listing.price.desc())

    listings = session.exec(statement).all()

    logger.info("Retrieved %d listings", len(listings))

    return {"listings": listings}


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
