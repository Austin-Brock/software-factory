"""Minimal Linear GraphQL client using only the standard library."""

import json
import urllib.request

API_URL = "https://api.linear.app/graphql"

ISSUE_QUERY = """
query Issue($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    url
    team { key }
    labels { nodes { id name } }
  }
}
"""

COMMENT_MUTATION = """
mutation Comment($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) { success }
}
"""


class LinearError(RuntimeError):
    pass


class LinearClient:
    def __init__(self, api_key: str, timeout: float = 15):
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, query: str, variables: dict) -> dict:
        request = urllib.request.Request(
            API_URL,
            data=json.dumps({"query": query, "variables": variables}).encode(),
            # Personal API keys go in the header as-is, without "Bearer".
            headers={"Authorization": self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read())
        if body.get("errors"):
            raise LinearError(body["errors"][0].get("message", "Linear API error"))
        return body["data"]

    def get_issue(self, issue_id: str) -> dict:
        return self._request(ISSUE_QUERY, {"id": issue_id})["issue"]

    def comment(self, issue_id: str, body: str) -> None:
        result = self._request(COMMENT_MUTATION, {"issueId": issue_id, "body": body})
        if not result["commentCreate"]["success"]:
            raise LinearError("commentCreate returned success=false")
