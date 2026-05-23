"""Sentry API Client for fetching performance and issues data."""

import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import List, Dict, Any, Optional
import urllib3
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class SentryClient:
    """Client for interacting with Sentry API across multiple projects."""

    def __init__(
        self,
        token: Optional[str] = None,
        org: Optional[str] = None,
        base_url: Optional[str] = None,
        default_project_slug: Optional[str] = None,
    ):
        self.token = token or os.getenv("SENTRY_TOKEN")
        self.org = org or os.getenv("SENTRY_ORG")
        self.base_url = (base_url or os.getenv("SENTRY_BASE_URL", "")).rstrip("/")
        self.default_project_slug = default_project_slug or os.getenv("SENTRY_DEFAULT_PROJECT_SLUG")

        if not self.token:
            raise ValueError("SENTRY_TOKEN is required")
        if not self.org:
            raise ValueError("SENTRY_ORG is required")
        if not self.base_url:
            raise ValueError("SENTRY_BASE_URL is required")

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        self._project_cache: Dict[str, str] = {}

    def _resolve_slug(self, project_slug: Optional[str]) -> str:
        slug = project_slug or self.default_project_slug
        if not slug:
            raise ValueError(
                "project_slug is required (or set SENTRY_DEFAULT_PROJECT_SLUG). "
                "Use list_projects to discover available slugs."
            )
        return slug

    def list_projects(self) -> List[Dict[str, Any]]:
        """List all projects in the organization."""
        url = f"{self.base_url}/api/0/organizations/{self.org}/projects/"
        projects: List[Dict[str, Any]] = []
        params: Dict[str, Any] = {"per_page": 100}
        cursor: Optional[str] = None
        try:
            while True:
                if cursor:
                    params["cursor"] = cursor
                response = self.session.get(
                    url, headers=self.headers, params=params, timeout=30, verify=False
                )
                response.raise_for_status()
                page = response.json()
                projects.extend(page)
                link = response.headers.get("Link", "")
                next_cursor = self._next_cursor(link)
                if not next_cursor:
                    break
                cursor = next_cursor
            for p in projects:
                if p.get("slug") and p.get("id"):
                    self._project_cache[p["slug"]] = str(p["id"])
            return projects
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to list projects: {e}")
            raise Exception(f"Failed to list projects: {e}")

    @staticmethod
    def _next_cursor(link_header: str) -> Optional[str]:
        for part in link_header.split(","):
            if 'rel="next"' in part and 'results="true"' in part:
                start = part.find("cursor=")
                if start == -1:
                    continue
                cursor = part[start + len("cursor="):]
                cursor = cursor.split(">")[0].split("&")[0]
                return cursor
        return None

    def _project_id_for_slug(self, project_slug: str) -> str:
        if project_slug in self._project_cache:
            return self._project_cache[project_slug]
        self.list_projects()
        if project_slug not in self._project_cache:
            raise ValueError(
                f"Project slug '{project_slug}' not found in org '{self.org}'. "
                "Use list_projects to see available slugs."
            )
        return self._project_cache[project_slug]

    def get_transactions(
        self,
        project_slug: Optional[str] = None,
        period: str = "24h",
        limit: int = 50,
        sort: str = "-tpm",
    ) -> List[Dict[str, Any]]:
        """Get transactions for a project."""
        slug = self._resolve_slug(project_slug)
        project_id = self._project_id_for_slug(slug)
        url = f"{self.base_url}/api/0/organizations/{self.org}/events/"
        params = {
            "statsPeriod": period,
            "project": project_id,
            "query": "event.type:transaction",
            "sort": ["-team_key_transaction", sort],
            "per_page": limit,
            "field": [
                "team_key_transaction",
                "transaction",
                "project",
                "transaction.op",
                "http.method",
                "tpm()",
                "p50()",
                "p95()",
                "failure_rate()",
                "apdex()",
                "count_unique(user)",
                "count_miserable(user)",
                "user_misery()",
            ],
            "referrer": "api.performance.landing-table",
        }

        try:
            logger.info(f"Fetching transactions for {slug}: {url}")
            response = self.session.get(
                url, headers=self.headers, params=params, timeout=30, verify=False
            )
            response.raise_for_status()
            data = response.json()
            transactions = data.get("data", [])
            logger.info(f"Fetched {len(transactions)} transactions")
            return transactions
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch transactions: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response text: {e.response.text}")
            raise Exception(f"Failed to fetch transactions: {e}")

    def get_event_details(
        self, event_id: str, project_slug: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get detailed info about a specific event including spans."""
        slug = self._resolve_slug(project_slug)
        url = f"{self.base_url}/api/0/organizations/{self.org}/events/{slug}:{event_id}/"

        try:
            logger.info(f"Fetching event details from: {url}")
            response = self.session.get(url, headers=self.headers, timeout=30, verify=False)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch event details (org endpoint): {e}")
            try:
                url = f"{self.base_url}/api/0/projects/{self.org}/{slug}/events/{event_id}/"
                logger.info(f"Trying fallback URL: {url}")
                response = self.session.get(url, headers=self.headers, timeout=30, verify=False)
                response.raise_for_status()
                return response.json()
            except Exception:
                raise Exception(f"Failed to fetch event details: {e}")

    def get_issues(
        self,
        project_slug: Optional[str] = None,
        period: str = "24h",
        limit: int = 100,
        query: str = "",
    ) -> List[Dict[str, Any]]:
        """Get issues/errors from Sentry for a project."""
        slug = self._resolve_slug(project_slug)
        url = f"{self.base_url}/api/0/projects/{self.org}/{slug}/issues/"
        params = {"statsPeriod": period, "query": query, "per_page": limit}

        try:
            response = self.session.get(
                url, headers=self.headers, params=params, timeout=30, verify=False
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise Exception(f"Failed to fetch issues: {e}")

    def get_issue_details(
        self, issue_id: str, project_slug: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get detailed info about a specific issue including stack traces."""
        slug = self._resolve_slug(project_slug)
        url = f"{self.base_url}/api/0/organizations/{self.org}/issues/{issue_id}/"

        try:
            logger.info(f"Fetching issue details from: {url}")
            response = self.session.get(url, headers=self.headers, timeout=30, verify=False)
            response.raise_for_status()
            issue_data = response.json()

            latest_event_id = None
            if isinstance(issue_data.get("lastEvent"), str):
                latest_event_id = issue_data.get("lastEvent")
            elif isinstance(issue_data.get("lastEvent"), dict):
                latest_event_id = (
                    issue_data.get("lastEvent", {}).get("id")
                    or issue_data.get("lastEvent", {}).get("eventID")
                )
            if not latest_event_id and issue_data.get("latestEvent"):
                if isinstance(issue_data.get("latestEvent"), str):
                    latest_event_id = issue_data.get("latestEvent")
                else:
                    latest_event_id = (
                        issue_data.get("latestEvent", {}).get("id")
                        or issue_data.get("latestEvent", {}).get("eventID")
                    )

            if not latest_event_id:
                events_url = (
                    f"{self.base_url}/api/0/organizations/{self.org}/issues/{issue_id}/events/"
                )
                events_response = self.session.get(
                    events_url,
                    headers=self.headers,
                    params={"per_page": 1},
                    timeout=30,
                    verify=False,
                )
                if events_response.status_code == 200:
                    events = events_response.json()
                    if events:
                        latest_event_id = events[0].get("id") or events[0].get("eventID")

            if latest_event_id:
                event_url = (
                    f"{self.base_url}/api/0/organizations/{self.org}/events/"
                    f"{slug}:{latest_event_id}/"
                )
                try:
                    event_response = self.session.get(
                        event_url, headers=self.headers, timeout=30, verify=False
                    )
                    event_response.raise_for_status()
                    issue_data["latestEventDetails"] = event_response.json()
                except requests.exceptions.RequestException as event_error:
                    logger.warning(f"Failed to fetch event details: {event_error}")
                    try:
                        event_url = (
                            f"{self.base_url}/api/0/projects/{self.org}/{slug}/events/"
                            f"{latest_event_id}/"
                        )
                        event_response = self.session.get(
                            event_url, headers=self.headers, timeout=30, verify=False
                        )
                        event_response.raise_for_status()
                        issue_data["latestEventDetails"] = event_response.json()
                    except Exception as fallback_error:
                        logger.warning(f"Fallback also failed: {fallback_error}")

            return issue_data
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch issue details: {e}")
            try:
                url = f"{self.base_url}/api/0/projects/{self.org}/{slug}/issues/{issue_id}/"
                response = self.session.get(url, headers=self.headers, timeout=30, verify=False)
                response.raise_for_status()
                return response.json()
            except Exception:
                raise Exception(f"Failed to fetch issue details: {e}")

    def analyze_slow_transactions(
        self,
        project_slug: Optional[str] = None,
        threshold_ms: int = 2000,
        period: str = "24h",
    ) -> Dict[str, Any]:
        """Analyze and group slow transactions by route."""
        slug = self._resolve_slug(project_slug)
        transactions = self.get_transactions(project_slug=slug, period=period)

        if not transactions:
            return {"error": "No transactions found", "project": slug}

        routes: Dict[str, Dict[str, Any]] = {}
        for trans in transactions:
            route = trans.get("transaction", "unknown")
            p95_duration = trans.get("p95()", 0) or 0
            p50_duration = trans.get("p50()", 0) or 0
            tpm = trans.get("tpm()", 0) or 0
            failure_rate = trans.get("failure_rate()", 0) or 0

            if route not in routes:
                routes[route] = {
                    "transaction": route,
                    "p95_ms": p95_duration,
                    "p50_ms": p50_duration,
                    "tpm": tpm,
                    "failure_rate": failure_rate,
                    "http_method": trans.get("http.method", "N/A"),
                    "transaction_op": trans.get("transaction.op", "N/A"),
                }

        slow_routes = []
        for route, data in routes.items():
            if data["p95_ms"] > threshold_ms:
                slow_routes.append(
                    {
                        "route": route,
                        "p95_ms": round(data["p95_ms"], 2),
                        "p50_ms": round(data["p50_ms"], 2),
                        "tpm": round(data["tpm"], 2),
                        "failure_rate": round(data["failure_rate"] * 100, 2),
                        "http_method": data["http_method"],
                        "transaction_op": data["transaction_op"],
                    }
                )

        slow_routes.sort(key=lambda x: x["p95_ms"], reverse=True)

        return {
            "project": slug,
            "total_transactions": len(transactions),
            "total_routes": len(routes),
            "slow_routes_count": len(slow_routes),
            "threshold_ms": threshold_ms,
            "period": period,
            "slow_routes": slow_routes,
        }

    def get_transaction_events(
        self,
        transaction_name: str,
        project_slug: Optional[str] = None,
        period: str = "24h",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get actual event IDs for a specific transaction."""
        slug = self._resolve_slug(project_slug)
        project_id = self._project_id_for_slug(slug)
        url = f"{self.base_url}/api/0/organizations/{self.org}/events/"
        params = {
            "statsPeriod": period,
            "project": project_id,
            "query": f'event.type:transaction transaction:"{transaction_name}"',
            "sort": "-transaction.duration",
            "per_page": limit,
            "field": [
                "id",
                "timestamp",
                "transaction",
                "transaction.duration",
                "transaction.op",
                "http.method",
            ],
        }

        try:
            response = self.session.get(
                url, headers=self.headers, params=params, timeout=30, verify=False
            )
            response.raise_for_status()
            return response.json().get("data", [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch transaction events: {e}")
            return []

    def get_route_detailed_traces(
        self,
        route: str,
        project_slug: Optional[str] = None,
        period: str = "24h",
        threshold_ms: int = 2000,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Get detailed traces for a specific route including all spans."""
        slug = self._resolve_slug(project_slug)
        events = self.get_transaction_events(
            route, project_slug=slug, period=period, limit=limit
        )

        if not events:
            return {"error": f"No events found for route: {route}", "project": slug}

        slow_events = [
            e for e in events if (e.get("transaction.duration") or 0) * 1000 >= threshold_ms
        ]

        if not slow_events:
            return {
                "project": slug,
                "route": route,
                "message": f"No events slower than {threshold_ms}ms found",
                "total_events": len(events),
                "traces": [],
            }

        traces = []
        for event in slow_events[:limit]:
            event_id = event.get("id")
            if not event_id:
                continue
            try:
                trace = self.get_transaction_trace(event_id, project_slug=slug)
                traces.append(trace)
            except Exception as e:
                logger.error(f"Failed to get trace for event {event_id}: {e}")
                continue

        return {
            "project": slug,
            "route": route,
            "period": period,
            "threshold_ms": threshold_ms,
            "total_events": len(events),
            "slow_events_count": len(slow_events),
            "traces_analyzed": len(traces),
            "traces": traces,
        }

    def get_transaction_trace(
        self, event_id: str, project_slug: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get detailed trace info for a transaction."""
        slug = self._resolve_slug(project_slug)
        event = self.get_event_details(event_id, project_slug=slug)

        if not event:
            return {"error": "Event not found", "project": slug}

        spans = []
        for entry in event.get("entries", []):
            if entry.get("type") == "spans":
                spans = entry.get("data", [])
                break

        analyzed_spans = []
        for span in spans:
            start = span.get("start_timestamp", 0)
            end = span.get("timestamp", 0)
            duration_ms = (end - start) * 1000 if start and end else 0

            analyzed_spans.append(
                {
                    "op": span.get("op", "unknown"),
                    "description": span.get("description", "N/A"),
                    "duration_ms": round(duration_ms, 2),
                    "tags": span.get("tags", {}),
                    "data": span.get("data", {}),
                }
            )

        analyzed_spans.sort(key=lambda x: x["duration_ms"], reverse=True)

        start_ts = event.get("startTimestamp")
        end_ts = event.get("endTimestamp")
        total_duration_ms = 0
        if start_ts and end_ts:
            total_duration_ms = (end_ts - start_ts) * 1000

        return {
            "project": slug,
            "event_id": event_id,
            "transaction": event.get("title") or event.get("transaction") or "Unknown",
            "total_duration_ms": total_duration_ms,
            "timestamp": event.get("dateReceived"),
            "spans_count": len(analyzed_spans),
            "spans": analyzed_spans[:20],
        }
