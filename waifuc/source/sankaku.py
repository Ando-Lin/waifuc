import os
import warnings
from typing import Optional, Iterator, List

from PIL import Image, UnidentifiedImageError
from hbutils.system import TemporaryDirectory, urlsplit

from .base import BaseDataSource
from ..model import ImageItem
from ..utils import get_requests_session, srequest, download_file

try:
    from typing import Literal
except (ImportError, ModuleNotFoundError):
    from typing_extensions import Literal


class NoURL(Exception):
    pass


class SankakuSource(BaseDataSource):
    def __init__(self, tags: List[str],
                 username: Optional[str] = None, password: Optional[str] = None, access_token: Optional[str] = None,
                 min_size: Optional[int] = 800, download_silent: bool = True, group_name: str = 'sankaku'):
        self.tags = tags
        self.username, self.password = username, password
        self.access_token = access_token

        self.min_size = min_size
        self.download_silent = download_silent
        self.group_name = group_name

        self.session = get_requests_session(headers={
            'Content-Type': 'application/json; charset=utf-8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Host': 'capi-v2.sankakucomplex.com',
            'X-Requested-With': 'com.android.browser',
        })
        self.free_session = get_requests_session()

    _FILE_URLS = [
        ('sample_url', 'sample_width', 'sample_height'),
        ('preview_url', 'preview_width', 'preview_height'),
        ('file_url', 'width', 'height'),
    ]

    def _select_url(self, data):
        if self.min_size is not None:
            f_url, f_width, f_height = None, None, None
            for url_name, width_name, height_name in self._FILE_URLS:
                if url_name in data and width_name in data and height_name in data:
                    url, width, height = data[url_name], data[width_name], data[height_name]
                    if width >= self.min_size and height >= self.min_size:
                        if f_url is None or width < f_width:
                            f_url, f_width, f_height = url, width, height

            if f_url is not None:
                return f_url

        if 'file_url' in data:
            return data['file_url']
        else:
            raise NoURL

    def _login(self):
        if self.access_token:
            self.session.headers.update({
                "Authorization": f"Bearer {self.access_token}",
            })
        elif self.username and self.password:
            resp = srequest(self.session, 'POST', 'https://login.sankakucomplex.com/auth/token',
                            json={"login": self.username, "password": self.password})
            resp.raise_for_status()
            login_data = resp.json()
            self.session.headers.update({
                "Authorization": f"{login_data['token_type']} {login_data['access_token']}",
            })

    def _iter(self) -> Iterator[ImageItem]:
        self._login()

        page = 1
        while True:
            resp = srequest(self.session, 'GET', 'https://capi-v2.sankakucomplex.com/posts', params={
                'lang': 'en',
                'page': str(page),
                'limit': '100',
                'tags': ' '.join(self.tags),
            })
            resp.raise_for_status()
            for data in resp.json():
                if 'image' not in data['file_type']:
                    continue

                try:
                    url = self._select_url(data)
                except NoURL:
                    continue

                with TemporaryDirectory() as td:
                    _, ext_name = os.path.splitext(urlsplit(url).filename)
                    filename = f'{self.group_name}_{data["id"]}{ext_name}'
                    td_file = os.path.join(td, filename)
                    try:
                        download_file(
                            url, td_file, desc=filename,
                            session=self.free_session, silent=self.download_silent
                        )
                        image = Image.open(td_file)
                        image.load()
                    except UnidentifiedImageError:
                        warnings.warn(f'Resource {data["id"]} unidentified as image, skipped.')
                        continue

                    meta = {
                        'sankaku': data,
                        'group_id': f'{self.group_name}_{data["id"]}',
                        'filename': filename,
                        'tags': {key: 1.0 for key in [t_item['name'] for t_item in data['tags']]}
                    }
                    yield ImageItem(image, meta)

            page += 1