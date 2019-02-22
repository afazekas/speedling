import logging
import requests

LOG = logging.getLogger(__name__)


def download_file(url, filename):
    r = requests.get(url, stream=True)
    with open(filename, 'wb') as f:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)


def download_origin_or_mirror(url, filename):
    # add logic for local mirror
    return download_file(url=url, filename=filename)
