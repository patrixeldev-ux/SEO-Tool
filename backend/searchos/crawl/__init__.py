"""Crawl engine: fetcher, robots.txt, sitemaps, HTML parser and the BFS crawler.

Nothing in this package knows about Sites, organisations or the database: it takes an explicit start URL and
the set of host names it may touch.
"""

USER_AGENT = "SearchOSBot/0.1 (+https://patrixel.com/searchos-bot)"
ROBOTS_PRODUCT = "SearchOSBot"  # the product token robots.txt rules are matched against
