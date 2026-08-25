def render_html(title: str, data: dict) -> str:
    """Same content as render_markdown, as a single self-contained HTML
    file (inline CSS, no external assets) -- for deliverables meant to be
    opened directly in a browser rather than read as Markdown source.
    """
    body_parts = []
    for section, fields in data.items():
        body_parts.append(f"<h2>{_escape(section)}</h2>")
        if isinstance(fields, dict):
            body_parts.append("<ul>")
            for key, value in fields.items():
                body_parts.append(f"<li><strong>{_escape(key)}:</strong> {_escape(value)}</li>")
            body_parts.append("</ul>")
        elif isinstance(fields, list):
            if not fields:
                body_parts.append("<p><em>(none)</em></p>")
            else:
                body_parts.append("<ul>")
                for item in fields:
                    if isinstance(item, dict):
                        rendered = ", ".join(f"<strong>{_escape(k)}:</strong> {_escape(v)}" for k, v in item.items())
                        body_parts.append(f"<li>{rendered}</li>")
                    else:
                        body_parts.append(f"<li>{_escape(item)}</li>")
                body_parts.append("</ul>")
        else:
            body_parts.append(f"<p>{_escape(fields)}</p>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_escape(title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 860px; margin: 2.5rem auto; padding: 0 1.5rem; color: #0F172A; line-height: 1.5; }}
  h1 {{ font-size: 1.6rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 1.8rem; border-bottom: 1px solid #E2E8F0; padding-bottom: 0.3rem; }}
  ul {{ padding-left: 1.2rem; }}
  li {{ margin-bottom: 0.3rem; }}
  strong {{ color: #4338CA; }}
</style>
</head>
<body>
<h1>{_escape(title)}</h1>
{"".join(body_parts)}
</body>
</html>
"""


def _escape(value) -> str:
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_markdown(title: str, data: dict) -> str:
    lines = [f"# {title}", ""]
    for section, fields in data.items():
        lines.append(f"## {section}")
        if isinstance(fields, dict):
            for key, value in fields.items():
                lines.append(f"- **{key}**: {value}")
        elif isinstance(fields, list):
            if not fields:
                lines.append("- (none)")
            for item in fields:
                if isinstance(item, dict):
                    rendered = ", ".join(f"**{k}**: {v}" for k, v in item.items())
                    lines.append(f"- {rendered}")
                else:
                    lines.append(f"- {item}")
        else:
            lines.append(f"- {fields}")
        lines.append("")
    return "\n".join(lines)
