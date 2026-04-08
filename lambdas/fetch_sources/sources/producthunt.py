# lambdas/fetch_sources/sources/producthunt.py
import requests
import boto3
from datetime import datetime, timezone, timedelta
from typing import List
from .base import BaseSource
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../shared'))
from models import RawNewsItem

AI_TOPICS = {"Artificial Intelligence", "Machine Learning", "Developer Tools",
             "Productivity", "Open Source", "Large Language Models"}

GRAPHQL_QUERY = """
query($cursor: String) {
  posts(order: NEWEST, after: $cursor) {
    edges {
      node {
        id name tagline description url votesCount createdAt
        topics { edges { node { name } } }
      }
    }
    pageInfo { endCursor hasNextPage }
  }
}
"""

class ProductHuntSource(BaseSource):
    def source_id(self) -> str:
        return "producthunt"

    def _get_token(self) -> str:
        ssm = boto3.client("ssm")
        return ssm.get_parameter(
            Name="/pulso-ia/producthunt-token", WithDecryption=True
        )["Parameter"]["Value"]

    def fetch(self, lookback_hours: int) -> List[RawNewsItem]:
        token = self._get_token()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json"}
        items, cursor, has_next = [], None, True

        while has_next:
            payload = {"query": GRAPHQL_QUERY, "variables": {"cursor": cursor}}
            resp = requests.post(
                "https://api.producthunt.com/v2/api/graphql",
                json=payload, headers=headers, timeout=15
            )
            data = resp.json()["data"]["posts"]
            for edge in data["edges"]:
                node = edge["node"]
                created = datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00"))
                if created < cutoff:
                    has_next = False
                    break
                topics = {e["node"]["name"] for e in node["topics"]["edges"]}
                if not topics.intersection(AI_TOPICS):
                    continue
                items.append(RawNewsItem(
                    title=node["name"],
                    url=node["url"],
                    source=self.source_id(),
                    published_at=created.isoformat(),
                    raw_content=f"{node['tagline']}. {(node['description'] or '')[:300]}"
                ))
            cursor = data["pageInfo"]["endCursor"]
            has_next = has_next and data["pageInfo"]["hasNextPage"]
        return items
