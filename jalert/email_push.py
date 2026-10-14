"""QQ email delivery for Journal Alert."""

from __future__ import annotations

import html
import os
import smtplib
import ssl
from email.message import EmailMessage

from .score import tier_of


def _article_url(item) -> str:
    if getattr(item, "url", ""):
        return item.url

    doi = getattr(item, "doi", "")
    if doi:
        return f"https://doi.org/{doi}"

    return ""


def _escape(value) -> str:
    return html.escape(str(value or ""))


def _tier_label(score: int, tiers: dict) -> str:
    tier = tier_of(score, tiers)

    if tier == "must_read":
        return "🔥 必读"
    if tier == "worth_reading":
        return "⭐ 值得一读"

    return "· 其他相关"


def _build_html(
    *,
    day: str,
    cfg: dict,
    entries: list[dict],
    fetched_total: int,
    matched_total: int,
) -> str:
    project = cfg.get("project", {}).get(
        "name",
        "护理科研文献日报",
    )

    tiers = cfg.get("tiers", {})
    max_items = int(cfg.get("email", {}).get("max_items", 20))

    selected = entries[:max_items]

    parts = [
        "<html><body>",
        f"<h1>{_escape(project)}</h1>",
        f"<p><strong>日期：</strong>{_escape(day)}</p>",
        (
            "<p>"
            f"本次抓取 <strong>{fetched_total}</strong> 篇；"
            f"关键词命中 <strong>{matched_total}</strong> 篇；"
            f"新增 <strong>{len(entries)}</strong> 篇。"
            "</p>"
        ),
        "<hr>",
    ]

    if not selected:
        parts.append("<p>今日无新增命中文献。</p>")

    for index, entry in enumerate(selected, start=1):
        item = entry["item"]
        scored = entry["scored"]

        tier_label = _tier_label(scored.score, tiers)
        url = _article_url(item)

        if url:
            title_html = (
                f'<a href="{_escape(url)}">'
                f"{_escape(item.title)}</a>"
            )
        else:
            title_html = _escape(item.title)

        topics = "、".join(
            getattr(scored, "labels", []) or []
        )

        parts.extend(
            [
                f"<h2>{tier_label}｜{index}. {title_html}</h2>",
                (
                    "<p>"
                    f"<strong>期刊：</strong>{_escape(item.journal)}<br>"
                    f"<strong>发表日期：</strong>{_escape(item.date or '未知')}<br>"
                    f"<strong>主题：</strong>{_escape(topics or '—')}<br>"
                    f"<strong>匹配得分：</strong>{_escape(scored.score)}"
                    "</p>"
                ),
            ]
        )

        if getattr(item, "authors", None):
            authors = ", ".join(item.authors[:8])

            if len(item.authors) > 8:
                authors += " 等"

            parts.append(
                f"<p><strong>作者：</strong>{_escape(authors)}</p>"
            )

        if getattr(item, "doi", ""):
            doi_link = f"https://doi.org/{item.doi}"

            parts.append(
                "<p>"
                "<strong>DOI：</strong>"
                f'<a href="{_escape(doi_link)}">'
                f"{_escape(item.doi)}</a>"
                "</p>"
            )

        abstract = (
            getattr(item, "abstract", "") or ""
        ).strip()

        parts.append("<h3>摘要</h3>")

        if abstract:
            parts.append(
                f"<p>{_escape(abstract)}</p>"
            )
        else:
            parts.append(
                "<p>⚠️ 当前公开数据源未获取到摘要。</p>"
            )

        parts.append("<hr>")

    if len(entries) > len(selected):
        parts.append(
            "<p>"
            f"另有 {len(entries) - len(selected)} 篇新增文献"
            "未在邮件正文中展开，可在 GitHub reports/ 日报中查看。"
            "</p>"
        )

    parts.extend(
        [
            "<p><small>"
            "本邮件由 Journal-alert 自动生成。"
            "摘要来源于公开文献数据源，正式引用前请核对原文。"
            "</small></p>",
            "</body></html>",
        ]
    )

    return "\n".join(parts)


def _build_plain(
    *,
    day: str,
    cfg: dict,
    entries: list[dict],
    fetched_total: int,
    matched_total: int,
) -> str:
    project = cfg.get("project", {}).get(
        "name",
        "护理科研文献日报",
    )

    tiers = cfg.get("tiers", {})
    max_items = int(cfg.get("email", {}).get("max_items", 20))

    lines = [
        f"{project}｜{day}",
        "",
        f"抓取：{fetched_total} 篇",
        f"关键词命中：{matched_total} 篇",
        f"新增：{len(entries)} 篇",
        "",
    ]

    for index, entry in enumerate(entries[:max_items], start=1):
        item = entry["item"]
        scored = entry["scored"]

        tier_label = _tier_label(
            scored.score,
            tiers,
        )

        topics = "、".join(
            getattr(scored, "labels", []) or []
        )

        lines.extend(
            [
                tier_label,
                f"{index}. {item.title}",
                f"期刊：{item.journal}",
                f"日期：{item.date or '未知'}",
                f"主题：{topics or '—'}",
                f"得分：{scored.score}",
            ]
        )

        if getattr(item, "authors", None):
            lines.append(
                "作者：" + ", ".join(item.authors[:8])
            )

        if getattr(item, "doi", ""):
            lines.append(
                f"DOI：https://doi.org/{item.doi}"
            )

        lines.append(
            f"原文：{_article_url(item)}"
        )

        lines.extend(
            [
                "",
                "【摘要】",
                getattr(item, "abstract", "")
                or "当前公开数据源未获取到摘要。",
                "",
                "-" * 60,
                "",
            ]
        )

    return "\n".join(lines)


def dispatch_email(
    *,
    day: str,
    cfg: dict,
    entries: list[dict],
    fetched_total: int,
    matched_total: int,
    log,
) -> dict:
    email_cfg = cfg.get("email", {})

    if not email_cfg.get("enabled", False):
        return {
            "ok": False,
            "detail": "email disabled",
        }

    username = os.environ.get(
        "JALERT_EMAIL_USERNAME",
        "",
    ).strip()

    password = os.environ.get(
        "JALERT_EMAIL_PASSWORD",
        "",
    ).strip()

    recipient = os.environ.get(
        "JALERT_EMAIL_TO",
        "",
    ).strip()

    if not username or not password or not recipient:
        return {
            "ok": False,
            "detail": (
                "missing JALERT_EMAIL_USERNAME / "
                "JALERT_EMAIL_PASSWORD / "
                "JALERT_EMAIL_TO"
            ),
        }

    smtp_host = str(
        email_cfg.get(
            "smtp_host",
            "smtp.qq.com",
        )
    )

    smtp_port = int(
        email_cfg.get(
            "smtp_port",
            465,
        )
    )

    prefix = str(
        email_cfg.get(
            "subject_prefix",
            "护理科研文献日报",
        )
    )

    subject = (
        f"{prefix}｜{day}｜新增 {len(entries)} 篇"
    )

    msg = EmailMessage()

    msg["Subject"] = subject
    msg["From"] = username
    msg["To"] = recipient

    msg.set_content(
        _build_plain(
            day=day,
            cfg=cfg,
            entries=entries,
            fetched_total=fetched_total,
            matched_total=matched_total,
        )
    )

    msg.add_alternative(
        _build_html(
            day=day,
            cfg=cfg,
            entries=entries,
            fetched_total=fetched_total,
            matched_total=matched_total,
        ),
        subtype="html",
    )

    try:
        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            context=context,
            timeout=60,
        ) as server:
            server.login(
                username,
                password,
            )
            server.send_message(msg)

    except Exception as exc:
        log.warning(
            "email delivery failed: %s",
            str(exc)[:300],
        )

        return {
            "ok": False,
            "detail": str(exc)[:300],
        }

    log.info(
        "email delivery OK -> %s",
        recipient,
    )

    return {
        "ok": True,
        "detail": recipient,
    }
