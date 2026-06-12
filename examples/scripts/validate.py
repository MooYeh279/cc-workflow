"""Validation script for complex workflow example.

Reads JSON context from stdin (containing upstream research outputs from
multiple nodes), validates the content, and outputs a combined result as
JSON to stdout.

Expected stdin JSON structure:
{
  "inputs": {"requirement": "..."},
  "upstream": {
    "research_A": {"summary_a": "...", "key_points_a": [...]},
    "research_B": {"summary_b": "...", "key_points_b": [...]}
  },
  "nodes": {...},
  "run": {"id": "...", "work_dir": "..."},
  "config": {...}
}
"""
import json
import sys


def main():
    try:
        raw = sys.stdin.read()
        context = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"validated": False, "error": f"Invalid stdin JSON: {e}"}))
        sys.exit(1)

    upstream = context.get("upstream", {})
    inputs = context.get("inputs", {})

    # Extract research results from both upstream nodes
    research_a = upstream.get("research_A", {})
    research_b = upstream.get("research_B", {})

    # Validate that both research nodes produced output
    errors = []
    if not research_a:
        errors.append("research_A produced no output")
    if not research_b:
        errors.append("research_B produced no output")

    # Combine findings
    combined_key_points = []
    for label, data in [("A", research_a), ("B", research_b)]:
        for kp in data.get("key_points", data.get("key_points_a", data.get("key_points_b", []))):
            combined_key_points.append(f"[{label}] {kp}")

    summary_a = research_a.get("summary", research_a.get("summary_a", ""))
    summary_b = research_b.get("summary", research_b.get("summary_b", ""))

    result = {
        "validated": len(errors) == 0,
        "errors": errors,
        "combined_summary": f"Research A: {summary_a}\nResearch B: {summary_b}",
        "combined_key_points": combined_key_points,
        "source_count": len(upstream),
        "requirement": inputs.get("requirement", ""),
    }

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
