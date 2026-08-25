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
