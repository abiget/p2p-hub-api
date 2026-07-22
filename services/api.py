import httpx


def get_binance_ad_data(
    client: httpx.Client, binance_ad_code: str, binance_api_url: str
) -> httpx.Response:
    payload = {"shareCode": binance_ad_code}

    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    response = client.post(binance_api_url, json=payload, headers=headers)

    return response
