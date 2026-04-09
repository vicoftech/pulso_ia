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
            try:
                body = resp.json()
            except ValueError as e:
                raise ValueError(f"Product Hunt: invalid JSON (HTTP {resp.status_code})") from e

            if resp.status_code >= 400:
                raise ValueError(
                    f"Product Hunt HTTP {resp.status_code}: {str(body)[:400]}"
                )

            data_root = body.get("data")
            if data_root is None:
                errs = body.get("errors") or []
                msg = errs[0].get("message", str(errs)) if errs else "data is null"
                raise ValueError(f"Product Hunt GraphQL: {msg}")

            posts = data_root.get("posts")
            if posts is None:
                break

            edges = posts.get("edges") or []
            page_info = posts.get("pageInfo") or {}

            for edge in edges:
                node = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(node, dict):
                    continue
                created_raw = node.get("createdAt")
                if not created_raw:
                    continue
                created = datetime.fromisoformat(
                    created_raw.replace("Z", "+00:00")
                )
                if created < cutoff:
                    has_next = False
                    break

                topic_edges = (node.get("topics") or {}).get("edges") or []
                topics = set()
                for te in topic_edges:
                    if not isinstance(te, dict):
                        continue
                    tn = te.get("node")
                    if isinstance(tn, dict) and tn.get("name"):
                        topics.add(tn["name"])

                if not topics.intersection(AI_TOPICS):
                    continue

                name = node.get("name") or "Untitled"
                url = node.get("url") or ""
                tagline = node.get("tagline") or ""
                desc = (node.get("description") or "")[:300]
                items.append(RawNewsItem(
                    title=name,
                    url=url,
                    source=self.source_id(),
                    published_at=created.isoformat(),
                    raw_content=f"{tagline}. {desc}"
                ))

            cursor = page_info.get("endCursor")
            has_next = has_next and bool(page_info.get("hasNextPage"))
        return items
