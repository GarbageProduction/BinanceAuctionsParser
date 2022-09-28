from __future__ import annotations  # Чтоб работали аннотации типов
from datetime import (
    datetime,
    timedelta,
)
import json
from typing import (
    Optional, List,
)

from config import (
    headers_no_token,
    collection_ids,
)

from aiohttp import (
    ClientSession,
)

from asyncio import (
    run,
    gather,
)


async def main() -> None:
    # create
    currency_converter = CurrencyConverter()
    processing = Processing(currency_converter)
    parser = Parser(processing, currency_converter, collection_ids)

    # init
    await currency_converter.init()

    # run
    await parser.parse_auction()


class Parser:
    def __init__(self, processing: Processing, currency_converter: CurrencyConverter,
                 collection_ids: List[str]) -> None:
        self.__processing = processing
        self.__currency_converter = currency_converter
        self.__collection_ids = collection_ids

    async def parse_auction(self):
        # trade-type 1 - auction
        # trade-type 0 - not auction
        tasks = []
        for collection_id in self.__collection_ids:
            task = self.__parse_collection(collection_id)
            tasks.append(task)
        await gather(*tasks)

    async def __parse_collection(self, collection_id: str) -> None:
        layer_product_list_auction = {'page': 1, 'rows': 16, 'collectionId': collection_id, 'tradeType': 1,
                                      'orderBy': 'set_end_time'}
        async with ClientSession(headers=headers_no_token) as session:
            async with session.post('https://www.binance.com/bapi/nft/v1/friendly/nft/layer-product-list',
                                    data=json.dumps(layer_product_list_auction)) as response:
                data = await response.json()

        # Список лотов на аукционе из конкретной коллекции
        rows = data['data']['rows']
        # Если аукционов в коллекции нет, берём следующую коллекцию
        if rows is None:
            return
        # Разделяем список лотов из коллекции на отдельные лоты
        tasks = []
        for row in rows:
            task = self.divide_lots(row, collection_id)
            tasks.append(task)
        await gather(*tasks)

    async def divide_lots(self, row: dict, collection_id: str) -> None:
        currency = row['currency']
        product_name = row['title']
        amount = float(row['amount'])
        product_id = row['productId']
        # Вычисляем время до начала аукциона
        now = datetime.now()
        end_time = datetime.fromtimestamp(int(row['setEndTime']) / 1000)
        duration = end_time - now

        # Вычисляем долларовую стоимость лота
        price_usd = self.__currency_converter.to_usdt(amount, currency)
        # Сравниваем минимальную стоимость предмета коллекции и цену аукциона
        x = await self.__processing.calculate_benefits(collection_id, product_name, price_usd)
        # Настройки
        min_x = 1
        if x is not None and x > min_x and duration < timedelta(hours=48):
            item = CollectionItem(product_id, product_name, currency, price_usd, duration, x)
            self.__processing.print_item(item)


class CollectionItem:
    def __init__(self, product_id, product_name, currency: str, price: float, duration: timedelta, x: float) -> None:
        self.product_id = product_id
        self.product_name = product_name
        self.currency = currency
        self.price = price
        self.duration = duration
        self.x = x


class Processing:
    def __init__(self, currency_converter: CurrencyConverter):
        self.__currency_converter = currency_converter

    async def calculate_benefits(self, collection_id: str, product_name: str, price: float) -> Optional[float]:
        """Поиск минимальной стоимости предмета из коллекции и расчёт иксов"""
        # trade-type 1 - auction
        # trade-type 0 - not auction
        layer_product_list_auction = {'page': 1, 'rows': 16, 'collectionId': collection_id, 'tradeType': 0,
                                      'orderBy': 'amount_sort', 'keyword': product_name}
        async with ClientSession(headers=headers_no_token) as session:
            async with session.post('https://www.binance.com/bapi/nft/v1/friendly/nft/layer-product-list',
                                    data=json.dumps(layer_product_list_auction)) as response:
                data = await response.json()
        rows = data['data']['rows']
        if rows is None:
            # no fixed prices, only auctions
            return None

        currency = rows[0]['currency']
        min_price = float(rows[0]['amount'])

        min_price_usd = self.__currency_converter.to_usdt(min_price, currency)
        return round(min_price_usd / price, 3)

    def print_item(self, item: CollectionItem):
        print(
            f'Наименование: {item.product_name}, Цена: {item.price} в {item.currency} | \
            До окончания осталось: {item.duration} | ID: {item.product_id} | Иксы: {item.x}')


class CurrencyConverter:
    def __init__(self):
        self.__prices = {}

    async def init(self) -> None:
        currencies = ['BUSD', 'BNB', 'ETH', 'HIGH']
        tasks = []
        for currency in currencies:
            task = self.__fetch_price(currency)
            tasks.append(task)
        await gather(*tasks)

    async def __fetch_price(self, currency: str) -> None:
        """Запрос на торговую пару: {'symbol': 'монета', 'price': 'её цена в долларах'} """
        currency_symbol = f'{currency}USDT'
        async with ClientSession() as session:
            async with session.get(
                    f'https://api.binance.com/api/v3/ticker/price?symbol={currency_symbol}') as response:
                data = await response.json()
        price = float(data['price'])  # цена монеты в долларах
        self.__prices[currency] = price

    def to_usdt(self, amount: float, currency: str) -> float:
        price = self.__prices[currency]
        return price * amount


run(main())
