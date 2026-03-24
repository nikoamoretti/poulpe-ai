"""Outbound Execution Service — actually SENDS tweets, emails, and publishes content.

Bridges the gap between agents that DRAFT content and the real world.
Handles: Twitter/X posts, cold email via Instantly.ai, transactional email via Resend,
and content publishing.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class OutboundService:
    """Executes outbound marketing actions on behalf of business agents."""

    def __init__(self) -> None:
        self.twitter_bearer = os.environ.get("TWITTER_BEARER_TOKEN", "")
        self.resend_api_key = os.environ.get("RESEND_API_KEY", "")
        self.resend_from = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
        self.instantly_api_key = os.environ.get("INSTANTLY_API_KEY", "")

    # ── Twitter/X ───────────────────────────────────────────────────

    def post_tweet(self, text: str) -> dict[str, Any]:
        """Post a tweet. Returns tweet ID and URL on success."""
        if not self.twitter_bearer:
            return {"status": "skipped", "reason": "TWITTER_BEARER_TOKEN not set"}
        if len(text) > 280:
            return {"status": "error", "reason": f"Tweet too long: {len(text)}/280"}

        try:
            with httpx.Client(
                base_url="https://api.twitter.com",
                headers={"Authorization": f"Bearer {self.twitter_bearer}"},
                timeout=15.0,
            ) as client:
                resp = client.post("/2/tweets", json={"text": text})
                resp.raise_for_status()
                data = resp.json()
                tweet_id = data.get("data", {}).get("id", "")
                return {
                    "status": "posted",
                    "tweet_id": tweet_id,
                    "url": f"https://twitter.com/i/web/status/{tweet_id}",
                }
        except Exception as exc:
            logger.exception("failed to post tweet")
            return {"status": "error", "reason": str(exc)}

    def post_thread(self, tweets: list[str]) -> dict[str, Any]:
        """Post a thread of tweets chained as replies."""
        if not self.twitter_bearer:
            return {"status": "skipped", "reason": "TWITTER_BEARER_TOKEN not set"}

        try:
            posted: list[dict[str, str]] = []
            reply_to: str | None = None

            with httpx.Client(
                base_url="https://api.twitter.com",
                headers={"Authorization": f"Bearer {self.twitter_bearer}"},
                timeout=15.0,
            ) as client:
                for tweet_text in tweets:
                    body: dict[str, Any] = {"text": tweet_text}
                    if reply_to:
                        body["reply"] = {"in_reply_to_tweet_id": reply_to}
                    resp = client.post("/2/tweets", json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    tweet_id = data.get("data", {}).get("id", "")
                    posted.append({
                        "tweet_id": tweet_id,
                        "url": f"https://twitter.com/i/web/status/{tweet_id}",
                    })
                    reply_to = tweet_id

            return {"status": "posted", "thread_length": len(posted), "tweets": posted}
        except Exception as exc:
            logger.exception("failed to post thread")
            return {"status": "error", "reason": str(exc)}

    # ── Email (Resend — transactional only) ─────────────────────────

    def send_email(
        self, to: str, subject: str, html: str, *, from_email: str | None = None
    ) -> dict[str, Any]:
        """Send a transactional email via Resend."""
        if not self.resend_api_key:
            return {"status": "skipped", "reason": "RESEND_API_KEY not set"}

        try:
            with httpx.Client(
                base_url="https://api.resend.com",
                headers={"Authorization": f"Bearer {self.resend_api_key}"},
                timeout=15.0,
            ) as client:
                resp = client.post("/emails", json={
                    "from": from_email or self.resend_from,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                })
                resp.raise_for_status()
                data = resp.json()
                return {"status": "sent", "email_id": data.get("id")}
        except Exception as exc:
            logger.exception("failed to send email")
            return {"status": "error", "reason": str(exc)}

    # ── Cold Email (Instantly.ai) ───────────────────────────────────

    def send_cold_email(
        self,
        campaign_id: str,
        to_email: str,
        to_name: str,
        *,
        variables: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Add a lead to an Instantly.ai campaign for cold outreach."""
        if not self.instantly_api_key:
            return {"status": "skipped", "reason": "INSTANTLY_API_KEY not set"}

        try:
            with httpx.Client(
                base_url="https://api.instantly.ai/api/v1",
                timeout=15.0,
            ) as client:
                resp = client.post("/lead/add", json={
                    "api_key": self.instantly_api_key,
                    "campaign_id": campaign_id,
                    "skip_if_in_workspace": True,
                    "leads": [{
                        "email": to_email,
                        "first_name": to_name.split()[0] if to_name else "",
                        "last_name": " ".join(to_name.split()[1:]) if to_name else "",
                        "variables": variables or {},
                    }],
                })
                resp.raise_for_status()
                data = resp.json()
                return {"status": "added_to_campaign", "campaign_id": campaign_id, "response": data}
        except Exception as exc:
            logger.exception("failed to add lead to Instantly campaign")
            return {"status": "error", "reason": str(exc)}

    def create_instantly_campaign(
        self,
        name: str,
        subject: str,
        body: str,
        *,
        from_account: str | None = None,
    ) -> dict[str, Any]:
        """Create a cold email campaign in Instantly.ai."""
        if not self.instantly_api_key:
            return {"status": "skipped", "reason": "INSTANTLY_API_KEY not set"}

        try:
            with httpx.Client(
                base_url="https://api.instantly.ai/api/v1",
                timeout=15.0,
            ) as client:
                campaign_body: dict[str, Any] = {
                    "api_key": self.instantly_api_key,
                    "name": name,
                    "sequences": [{
                        "steps": [{
                            "type": "email",
                            "delay": 0,
                            "variants": [{
                                "subject": subject,
                                "body": body,
                            }],
                        }],
                    }],
                }
                resp = client.post("/campaign/create", json=campaign_body)
                resp.raise_for_status()
                data = resp.json()
                return {"status": "created", "campaign_id": data.get("id"), "name": name}
        except Exception as exc:
            logger.exception("failed to create Instantly campaign")
            return {"status": "error", "reason": str(exc)}

    def get_instantly_analytics(self, campaign_id: str) -> dict[str, Any]:
        """Get analytics for an Instantly.ai campaign."""
        if not self.instantly_api_key:
            return {"status": "skipped", "reason": "INSTANTLY_API_KEY not set"}

        try:
            with httpx.Client(
                base_url="https://api.instantly.ai/api/v1",
                timeout=15.0,
            ) as client:
                resp = client.get("/analytics/campaign/summary", params={
                    "api_key": self.instantly_api_key,
                    "campaign_id": campaign_id,
                })
                resp.raise_for_status()
                return {"status": "ok", **resp.json()}
        except Exception as exc:
            logger.exception("failed to get Instantly analytics")
            return {"status": "error", "reason": str(exc)}

    # ── Parse and execute agent output ──────────────────────────────

    def execute_marketing_actions(
        self,
        agent_result: dict[str, Any],
        business_name: str,
    ) -> dict[str, Any]:
        """Parse a marketing agent's output and execute real actions.

        Looks for structured content in the agent's result and fires
        the appropriate outbound channel.
        """
        executed: list[dict[str, Any]] = []
        result_data = agent_result.get("result", {})
        if not isinstance(result_data, dict):
            return {"actions_executed": 0, "results": []}

        content_type = result_data.get("content_type", "")
        content = result_data.get("content", {})

        # Handle tweets
        if content_type == "tweet" or "tweet" in str(result_data.get("result", "")):
            tweet_text = ""
            if isinstance(content, str):
                tweet_text = content
            elif isinstance(content, dict):
                tweet_text = content.get("text", content.get("tweet", ""))

            if tweet_text:
                r = self.post_tweet(tweet_text)
                executed.append({"action": "tweet", **r})

        # Handle threads
        if content_type == "thread" or "thread" in str(result_data.get("result", "")):
            tweets = []
            if isinstance(content, dict):
                tweets = content.get("tweets", [])
            elif isinstance(content, list):
                tweets = content

            if tweets:
                r = self.post_thread(tweets)
                executed.append({"action": "thread", **r})

        # Handle email content
        if content_type == "email" or "email" in str(result_data.get("result", "")):
            if isinstance(content, dict):
                to = content.get("to", "")
                subject = content.get("subject", f"[{business_name}] Update")
                html = content.get("html", content.get("body", ""))
                if to and html:
                    r = self.send_email(to, subject, html)
                    executed.append({"action": "email", **r})

        # Handle cold outreach campaigns
        if content_type == "cold_email" or "outreach" in str(result_data.get("result", "")):
            if isinstance(content, dict):
                campaign_name = content.get("campaign_name", f"{business_name} Outreach")
                subject = content.get("subject", "")
                body = content.get("body", "")
                if subject and body:
                    r = self.create_instantly_campaign(campaign_name, subject, body)
                    executed.append({"action": "cold_email_campaign", **r})

                    # Add leads if provided
                    leads = content.get("leads", [])
                    campaign_id = r.get("campaign_id")
                    if campaign_id and leads:
                        for lead in leads[:50]:  # Cap at 50 per cycle
                            if isinstance(lead, dict) and lead.get("email"):
                                lr = self.send_cold_email(
                                    campaign_id,
                                    lead["email"],
                                    lead.get("name", ""),
                                    variables=lead.get("variables", {}),
                                )
                                executed.append({"action": "add_lead", **lr})

        # If the content doesn't match known types, store as draft
        if not executed:
            executed.append({
                "action": "stored_as_draft",
                "content_type": content_type or "unknown",
                "preview": str(content)[:500] if content else "empty",
            })

        return {"actions_executed": len(executed), "results": executed}
