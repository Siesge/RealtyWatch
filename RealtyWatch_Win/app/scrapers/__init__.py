from .avito import Avito
from .cian import Cian
from .yandex import Yandex

SCRAPERS = {"cian": Cian, "avito": Avito, "yandex": Yandex}
