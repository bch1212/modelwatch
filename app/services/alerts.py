"""Alert dispatch — email (SendGrid) + Slack webhook on drift events."""

import html
import logging
from typing import Optional

import httpx
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import get_settings

logger = logging.getLogger(__name__)


async def send_email_alert(
    to_email: str,
    spec_name: str,
    endpoint_name: str,
    severity: str,
    drift_score: float,
    summary: str,
) -> bool:
    """Send a drift alert email via SendGrid."""
    settings = get_settings()
    if not settings.sendgrid_api_key:
        logger.warning("SendGrid not configured, skipping email alert")
        return False

    subject = f"[ModelWatch] {severity.upper()} drift detected: {spec_name}"
    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 600px;">
        <h2 style="color: #ef4444;">Behavioral Drift Detected</h2>
        <table style="width: 100%; border-collapse: collapse;">
            <tr><td style="padding: 8px; font-weight: bold;">Spec</td><td style="padding: 8px;">{spec_name}</td></tr>
            <tr><td style="padding: 8px; font-weight: bold;">Endpoint</td><td style="padding: 8px;">{endpoint_name}</td></tr>
            <tr><td style="padding: 8px; font-weight: bold;">Severity</td><td style="padding: 8px;">{severity.upper()}</td></tr>
            <tr><td style="padding: 8px; font-weight: bold;">Drift Score</td><td style="padding: 8px;">{drift_score:.3f}</td></tr>
        </table>
        <h3>What Changed</h3>
        <p>{summary}</p>
        <p style="color: #6b7280; font-size: 14px; margin-top: 24px;">
            View details in your <a href="https://modelwatch.dev/dashboard">ModelWatch dashboard</a>.
        </p>
    </div>
    """

    msg = Mail(
        from_email=settings.sendgrid_from_email,
        to_emails=to_email,
        subject=subject,
        html_content=html,
    )
    try:
        sg = SendGridAPIClient(settings.sendgrid_api_key)
        sg.send(msg)
        return True
    except Exception as exc:
        logger.error(f"SendGrid error: {exc}")
        return False


async def send_slack_alert(
    webhook_url: str,
    spec_name: str,
    endpoint_name: str,
    severity: str,
    drift_score: float,
    summary: str,
) -> bool:
    """Send a drift alert to Slack via incoming webhook."""
    severity_emoji = {
        "low": ":large_yellow_circle:",
        "medium": ":warning:",
        "high": ":red_circle:",
        "critical": ":rotating_light:",
    }
    emoji = severity_emoji.get(severity, ":question:")

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} ModelWatch Drift Alert",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Spec:*\n{spec_name}"},
                    {"type": "mrkdwn", "text": f"*Endpoint:*\n{endpoint_name}"},
                    {"type": "mrkdwn", "text": f"*Severity:*\n{severity.upper()}"},
                    {"type": "mrkdwn", "text": f"*Drift Score:*\n{drift_score:.3f}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Summary:*\n{summary}"},
            },
        ],
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(webhook_url, json=payload, timeout=10)
            return resp.status_code == 200
    except Exception as exc:
        logger.error(f"Slack webhook error: {exc}")
        return False


async def dispatch_alerts(
    alert_email: Optional[str],
    slack_webhook_url: Optional[str],
    spec_name: str,
    endpoint_name: str,
    severity: str,
    drift_score: float,
    summary: str,
) -> dict:
    """Send all configured alerts. Returns which channels succeeded."""
    result = {"email_sent": False, "slack_sent": False}

    if alert_email:
        result["email_sent"] = await send_email_alert(
            to_email=alert_email,
            spec_name=spec_name,
            endpoint_name=endpoint_name,
            severity=severity,
            drift_score=drift_score,
            summary=summary,
        )

    if slack_webhook_url:
        result["slack_sent"] = await send_slack_alert(
            webhook_url=slack_webhook_url,
            spec_name=spec_name,
            endpoint_name=endpoint_name,
            severity=severity,
            drift_score=drift_score,
            summary=summary,
        )

    return result


def _flat(value: str, limit: int = 300) -> str:
    return " ".join(value.replace("`", "'").split())[:limit]


async def dispatch_tool_schema_alert(
    alert_email: Optional[str],
    slack_webhook_url: Optional[str],
    tool_name: str,
    agent_id: str,
    summary: str,
) -> dict:
    """Alert that a monitored tool's response shape changed."""
    result = {"email_sent": False, "slack_sent": False}
    tool = _flat(tool_name, 200)
    agent = _flat(agent_id, 100)
    text = _flat(summary, 1000)

    if alert_email:
        result["email_sent"] = await _send_tool_schema_email(alert_email, tool, agent, text)
    if slack_webhook_url:
        result["slack_sent"] = await _send_tool_schema_slack(
            slack_webhook_url, tool, agent, text
        )
    return result


async def _send_tool_schema_email(to_email: str, tool_name: str, agent_id: str, summary: str) -> bool:
    settings = get_settings()
    if not settings.sendgrid_api_key:
        logger.warning("SendGrid not configured, skipping tool schema email")
        return False

    safe_tool = html.escape(tool_name)
    safe_agent = html.escape(agent_id)
    safe_summary = html.escape(summary)
    subject = f"[ModelWatch] Tool schema drift: {tool_name}"
    body = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 600px;">
        <h2 style="color: #ef4444;">Tool schema drift</h2>
        <p>A monitored tool returned a response shape that is not the dominant shape from the last 24 hours.</p>
        <table style="width: 100%; border-collapse: collapse;">
            <tr><td style="padding: 8px; font-weight: bold;">Tool</td><td style="padding: 8px;">{safe_tool}</td></tr>
            <tr><td style="padding: 8px; font-weight: bold;">Agent</td><td style="padding: 8px;">{safe_agent}</td></tr>
        </table>
        <h3>What changed</h3>
        <p>{safe_summary}</p>
        <p style="color: #6b7280; font-size: 14px; margin-top: 24px;">
            Details are in your <a href="https://modelwatch.app/dashboard.html">ModelWatch dashboard</a>.
        </p>
    </div>
    """
    msg = Mail(
        from_email=settings.sendgrid_from_email,
        to_emails=to_email,
        subject=subject,
        html_content=body,
    )
    try:
        sg = SendGridAPIClient(settings.sendgrid_api_key)
        sg.send(msg)
        return True
    except Exception as exc:
        logger.error(f"SendGrid error: {exc}")
        return False


async def _send_tool_schema_slack(
    webhook_url: str, tool_name: str, agent_id: str, summary: str
) -> bool:
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "ModelWatch tool schema drift"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Tool:*\n{tool_name}"},
                    {"type": "mrkdwn", "text": f"*Agent:*\n{agent_id}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Summary:*\n{summary}"},
            },
        ],
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(webhook_url, json=payload, timeout=10)
            return resp.status_code == 200
    except Exception as exc:
        logger.error(f"Slack webhook error: {exc}")
        return False
