def render_markdown(title: str, data: dict) -> str:
    lines = [f"# {title}", ""]
    for column, fields in data.items():
        lines.append(f"## {column}")
        for key, value in fields.items():
            lines.append(f"- **{key}**: {value}")
        lines.append("")
    return "\n".join(lines)
