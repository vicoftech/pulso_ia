# lambdas/fetch_sources/sources/github_trending.py
import requests
import boto3
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import List
from .base import BaseSource
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../shared'))
from models import RawNewsItem

AI_TOPIC_QUERIES = [
    "topic:llm+stars:>50",
    "topic:ai+topic:agents+stars:>50",
    "topic:machine-learning+stars:>100",
    "topic:generative-ai+stars:>30",
]

class GitHubTrendingSource(BaseSource):
    def source_id(self) -> str:
        return "github"

    def _get_token(self) -> str:
        ssm = boto3.client("ssm")
        return ssm.get_parameter(
            Name="/pulso-ia/github-token", WithDecryption=True
        )["Parameter"]["Value"]

    def _search(self, query: str, date_str: str, headers: dict) -> List[RawNewsItem]:
        url = "https://api.github.com/search/repositories"
        params = {"q": f"{query}+pushed:>{date_str}",
                  "sort": "stars", "order": "desc", "per_page": 20}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        items = []
        for repo in resp.json().get("items", []):
            pushed = datetime.fromisoformat(repo["pushed_at"].replace("Z", "+00:00"))
            owner = repo.get("owner") or {}
            avatar = owner.get("avatar_url")
            if avatar and not isinstance(avatar, str):
                avatar = None
            items.append(RawNewsItem(
                title=f"{repo['full_name']} - {repo['stargazers_count']}*",
                url=repo["html_url"],
                source=self.source_id(),
                published_at=pushed.isoformat(),
                raw_content=(
                    f"{repo.get('description', '')}. "
                    f"Topics: {', '.join(repo.get('topics', []))}"
                ),
                image_url=avatar,
            ))
        return items

    def fetch(self, lookback_hours: int) -> List[RawNewsItem]:
        token = self._get_token()
        headers = {"Authorization": f"token {token}",
                   "Accept": "application/vnd.github.v3+json"}
        date_str = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
                    ).strftime("%Y-%m-%d")
        seen_urls, results = set(), []
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(self._search, q, date_str, headers)
                       for q in AI_TOPIC_QUERIES]
            for f in futures:
                for item in f.result():
                    if item.url not in seen_urls:
                        seen_urls.add(item.url)
                        results.append(item)
        return results
