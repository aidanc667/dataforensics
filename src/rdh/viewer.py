def classify_report(data: dict) -> str:
    if "mutations" in data and "run_id" in data:
        return "manifest"
    if {"errors", "warnings", "suggestions"} <= data.keys():
        return "validation_report"
    if data and all(isinstance(v, dict) for v in data.values()):
        return "data_dictionary"
    return "unknown"


def validation_summary(data: dict) -> dict:
    return {
        "errors": len(data.get("errors", [])),
        "warnings": len(data.get("warnings", [])),
        "suggestions": len(data.get("suggestions", [])),
        "checks_evaluated": data.get("checks_evaluated", 0),
        "checks_passed": data.get("checks_passed", 0),
    }
