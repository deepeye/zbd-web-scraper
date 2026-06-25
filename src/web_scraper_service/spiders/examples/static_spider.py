"""Example spider for static HTML pages — quotes.toscrape.com."""

from __future__ import annotations

from typing import Any

from scrapling.engines.toolbelt.custom import Response

from web_scraper_service.spiders.base import BaseSpider
from web_scraper_service.spiders.registry import register_spider


@register_spider
class QuotesSpider(BaseSpider):
    name = "quotes_static"
    start_urls = ["https://quotes.toscrape.com/"]
    use_playwright = False
    use_stealthy = True

    async def parse(self, response: Response, **kwargs: Any) -> Any:
        for quote in response.css(".quote"):
            text = quote.css(".text::text").get()
            author = quote.css(".author::text").get()
            tags = quote.css(".tag::text").getall()
            yield {
                "url": kwargs.get("url", ""),
                "text": str(text).strip() if text else "",
                "author": str(author).strip() if author else "",
                "tags": [str(t).strip() for t in tags] if tags else [],
            }

        # Follow pagination
        next_link = response.css("li.next a::attr(href)").get()
        if next_link:
            next_url = str(next_link)
            if not next_url.startswith("http"):
                next_url = f"https://quotes.toscrape.com{next_url}"
            async for item in self.parse(await self.fetch(next_url), url=next_url):
                yield item
