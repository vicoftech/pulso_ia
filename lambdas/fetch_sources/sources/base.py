# lambdas/fetch_sources/sources/base.py
from abc import ABC, abstractmethod
from typing import List
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../shared'))
from models import RawNewsItem

class BaseSource(ABC):
    @abstractmethod
    def fetch(self, lookback_hours: int) -> List[RawNewsItem]:
        pass

    @abstractmethod
    def source_id(self) -> str:
        pass
