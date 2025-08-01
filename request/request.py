import datetime
from fake_useragent import UserAgent
from request.common import HSCategoryMapper, IsRateLimited, PlayerRecord, RequestFailed, HSLookup, HSApi

import threading
import requests
from bs4 import BeautifulSoup
from request.extract import extract_highscore_records
from util.log import get_logger
from util.retry_handler import retry

logger = get_logger()
proxy_lock = threading.Lock()


class Requests():
    def __init__(self, proxy_list:  list | None = None):
        self.proxy_list = proxy_list
        self.proxy_idx = 0

    def get_proxies(self) -> dict | None:
        if not self.proxy_list or len(self.proxy_list) == 0:
            return None

        with proxy_lock:
            proxy = self.proxy_list[self.proxy_idx]
            self.proxy_idx = (self.proxy_idx + 1) % len(self.proxy_list)

        return {
            "http": proxy,
            "https": proxy,
        }

    def find_max_page(self, account_type: HSLookup, hs_type: HSCategoryMapper) -> int:
        # max on hs is currently 80_000 pages
        l, r, res, page_size = 1, 100_000, -1, 25

        def give_first_idx(account_type, hs_type, middle):
            page = self.get_hs_page(account_type, hs_type, middle)
            extracted_records = extract_highscore_records(page)
            return -1 if not extracted_records else extracted_records[0].rank

        while l <= r:
            middle = (l + r) >> 1
            first_idx = retry(give_first_idx, account_type=account_type,
                              hs_type=hs_type, middle=middle)
            expected_idx = (middle - 1) * page_size + 1

            if first_idx == expected_idx:
                res = middle
                l = middle + 1
            else:
                r = middle - 1
            logger.info(f'page range: ({l}-{r})')
        return res

    def get_user_stats(self, name: str, account_type: HSApi, **kwargs) -> PlayerRecord:
        csv = self.lookup(name, account_type.csv()).split('\n')

        return PlayerRecord(username=name, csv=csv, ts=datetime.datetime.now(datetime.timezone.utc))

    def get_hs_page(self, account_type: HSLookup, hs_type: HSCategoryMapper, page_nr: int = 1) -> bytes:
        params = {'category_type': hs_type.get_category(),
                  'table': hs_type.value, 'page': page_nr, }
        page = self.https_request(account_type.overall(), params)
        return page

    def lookup(self, name: str, url: str) -> str:
        params = {'player': name}
        res = self.https_request(url, params)
        return res

    def https_request(self, url: str, params: dict) -> str:
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Origin, X-Requested-With, Content-Type, Accept",
            "Content-Type": "text/html",
            'User-Agent': UserAgent().random,
        }

        proxies = self.get_proxies()
        resp = requests.get(url, headers=headers,
                            params=params, proxies=proxies)

        text = resp.text.replace('Ā', ' ').replace('\xa0', ' ')

        if self.is_rate_limited(text):
            raise IsRateLimited(
                f"limited on \'{url}\'", details={"params": params, "proxies": proxies})

        if resp.status_code == 200:
            return text

        raise RequestFailed(f"failed on \'{url}\'", details={
                            "code": resp.status_code, "params": params, "proxies": proxies})

    def is_rate_limited(self, page: bytes):
        return "your IP has been temporarily blocked" in BeautifulSoup(page, "html.parser").text
