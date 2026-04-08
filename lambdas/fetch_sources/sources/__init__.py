from .arxiv import ArxivSource
from .producthunt import ProductHuntSource
from .github_trending import GitHubTrendingSource
from .rss import RSSSource

SOURCE_REGISTRY = {
    "arxiv":       ArxivSource,
    "producthunt": ProductHuntSource,
    "github":      GitHubTrendingSource,
    "rss":         RSSSource,
}
