"""People-signal providers (org chart, leadership, recent hires).

Note: LinkedIn proper is consumed plugin-side via the LinkedIn MCP, not by this kitchen
service. The kitchen handles signals where shared cache + bearer-scoped API access matters
(Apollo for headcount + leadership, etc.). Concrete providers land in follow-up PRs.
"""
